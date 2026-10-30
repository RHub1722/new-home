"""Очередь уведомлений (спека §7.1, §13): сообщения ждут в notifications, пока Telegram их не примет.

enqueue не делает commit. deliver фиксирует каждое сообщение сразу после отправки, чтобы сбой
посреди отправки не повторил уже доставленное. Длинное сообщение уходит частями (split_text): число
доставленных частей хранится в payload["sent_parts"] и фиксируется после каждой, так что следующая
попытка продолжает с первой недоставленной части.
"""

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from homealize.config import TelegramSettings
from homealize.notify.telegram import TelegramClient, TelegramError, split_text

log = logging.getLogger(__name__)

MAX_RETRY_AFTER = 60.0  # дольше ждать Telegram в этой отправке не будем: остальное уйдёт позже


@dataclass(frozen=True)
class Outgoing:
    key: str  # уникален: то же событие второй раз в очередь не попадёт
    kind: str  # new | price_drop | first_run | digest | alert
    text: str  # HTML
    listing_id: str | None = None
    topic: str | None = None  # вид тревоги или дата дайджеста
    preview_url: str | None = None  # фото над текстом
    data: dict = field(default_factory=dict)  # что ещё сохранить в payload (например, топ дайджеста); не sent_parts


@dataclass(frozen=True)
class Notifier:
    client: TelegramClient
    chat_id: int


@dataclass
class Delivery:
    sent: int = 0
    failed: int = 0  # временный сбой: сообщение осталось в очереди
    dropped: int = 0  # больше не будет отправляться


def enqueue(conn: sqlite3.Connection, items: list[Outgoing], *, now: datetime) -> int:
    """Ставит сообщения в очередь; уже известные ключи пропускаются. Возвращает, сколько добавлено."""
    created = now.astimezone(UTC).isoformat()
    added = 0
    for item in items:
        payload = {"text": item.text, "preview_url": item.preview_url, **item.data}
        cursor = conn.execute(
            "INSERT OR IGNORE INTO notifications (key, kind, topic, listing_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item.key, item.kind, item.topic, item.listing_id, json.dumps(payload, ensure_ascii=False), created),
        )
        added += cursor.rowcount
    return added


def deliver(
    conn: sqlite3.Connection,
    notifier: Notifier,
    settings: TelegramSettings,
    *,
    now: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> Delivery:
    """Отправляет ожидающие сообщения по порядку. При временном сбое останавливается: остальные уйдут позже."""
    delivery = Delivery()
    stamp = now.astimezone(UTC).isoformat()
    expire_before = (now.astimezone(UTC) - timedelta(hours=settings.expire_hours)).isoformat()
    pending = conn.execute("SELECT * FROM notifications WHERE status = 'pending' ORDER BY id").fetchall()
    for row in pending:
        if row["created_at"] < expire_before:
            _set(conn, row["id"], status="dropped", attempts=row["attempts"], error="устарело: не отправлено за сутки")
            delivery.dropped += 1
            continue
        if delivery.sent:
            sleep(settings.send_pause_seconds)
        payload = json.loads(row["payload"])
        try:
            _send(conn, notifier, row["id"], payload, sleep)
        except TelegramError as exc:
            attempts = row["attempts"] + 1
            give_up = exc.permanent or attempts >= settings.max_attempts
            _set(conn, row["id"], status="dropped" if give_up else "pending", attempts=attempts, error=str(exc))
            log.warning("Уведомление %s не отправлено: %s", row["key"], exc)
            if give_up:
                delivery.dropped += 1
            else:
                delivery.failed += 1
            if exc.permanent:
                continue  # плохо только это сообщение
            break  # Telegram недоступен: остальные — в следующий раз
        conn.execute(
            "UPDATE notifications SET status = 'sent', attempts = attempts + 1, sent_at = ?, last_error = NULL "
            "WHERE id = ?",
            (stamp, row["id"]),
        )
        conn.commit()
        delivery.sent += 1
    return delivery


def exists(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute("SELECT 1 FROM notifications WHERE key = ?", (key,)).fetchone() is not None


def status(conn: sqlite3.Connection, key: str) -> str | None:
    """Состояние сообщения с этим ключом: pending | sent | dropped; None — такого нет."""
    row = conn.execute("SELECT status FROM notifications WHERE key = ?", (key,)).fetchone()
    return row["status"] if row is not None else None


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE status = 'pending'").fetchone()[0]


def last_created(conn: sqlite3.Connection, kind: str, topic: str) -> datetime | None:
    """Когда последний раз ставилось в очередь сообщение этого вида и темы (в любом статусе)."""
    value = conn.execute(
        "SELECT MAX(created_at) FROM notifications WHERE kind = ? AND topic = ?", (kind, topic)
    ).fetchone()[0]
    return datetime.fromisoformat(value) if value else None


def _send(
    conn: sqlite3.Connection, notifier: Notifier, notification_id: int, payload: dict, sleep: Callable[[float], None]
) -> None:
    """Отправляет недоставленные части сообщения; после каждой, кроме последней, запоминает прогресс."""
    parts = split_text(payload["text"])
    for index in range(payload.get("sent_parts", 0), len(parts)):
        preview_url = payload.get("preview_url") if index == 0 else None  # фото — только у первой части
        _send_part(notifier, parts[index], preview_url, sleep)
        if index + 1 < len(parts):
            payload["sent_parts"] = index + 1
            conn.execute(
                "UPDATE notifications SET payload = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), notification_id),
            )
            conn.commit()


def _send_part(notifier: Notifier, text: str, preview_url: str | None, sleep: Callable[[float], None]) -> None:
    try:
        notifier.client.send_message(notifier.chat_id, text, preview_url=preview_url)
    except TelegramError as exc:
        if exc.retry_after is None or exc.retry_after > MAX_RETRY_AFTER:
            raise
        sleep(exc.retry_after)
        notifier.client.send_message(notifier.chat_id, text, preview_url=preview_url)


def _set(conn: sqlite3.Connection, notification_id: int, *, status: str, attempts: int, error: str) -> None:
    conn.execute(
        "UPDATE notifications SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
        (status, attempts, error, notification_id),
    )
    conn.commit()
