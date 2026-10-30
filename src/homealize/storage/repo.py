"""Чтение и запись объявлений, истории цен и журнала прогонов.

Функции не делают commit: транзакциями управляет вызывающий (pipeline).
"""

import json
import sqlite3
from datetime import datetime
from enum import StrEnum

from homealize.models import Listing, RunSummary


class UpsertOutcome(StrEnum):
    NEW = "new"
    PRICE_CHANGED = "price_changed"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


def upsert_listing(
    conn: sqlite3.Connection,
    listing: Listing,
    *,
    status: str,
    filter_reason: str | None,
    now: datetime,
) -> UpsertOutcome:
    """Сохраняет объявление; при смене цены пишет price_history. Сбрасывает счётчик пропусков."""
    values = _content(listing)
    ts = now.isoformat()
    existing = get_listing(conn, listing.id)
    if existing is None:
        columns = ["id", *values, "first_seen_at", "last_seen_at", "missed_scans", "status", "filter_reason"]
        params = [listing.id, *values.values(), ts, ts, 0, status, filter_reason]
        conn.execute(
            f"INSERT INTO listings ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            params,
        )
        _record_price(conn, listing, ts)
        return UpsertOutcome.NEW

    last_price = _last_price(conn, listing.id)
    price_changed = listing.price_original is not None and (
        last_price is None
        or (last_price["price_original"], last_price["currency"]) != (listing.price_original, listing.currency)
    )
    content_changed = any(existing[column] != value for column, value in values.items())
    state_changed = existing["status"] != status or existing["filter_reason"] != filter_reason
    assignments = ", ".join(f"{column} = ?" for column in values)
    conn.execute(
        f"UPDATE listings SET {assignments}, last_seen_at = ?, missed_scans = 0, status = ?, filter_reason = ? "
        "WHERE id = ?",
        [*values.values(), ts, status, filter_reason, listing.id],
    )
    if price_changed:
        _record_price(conn, listing, ts)
        return UpsertOutcome.PRICE_CHANGED
    return UpsertOutcome.UPDATED if content_changed or state_changed else UpsertOutcome.UNCHANGED


def mark_unseen(conn: sqlite3.Connection, seen_ids: set[str], *, threshold: int) -> int:
    """После полного прохода: +1 пропуск всем, кого не видели; снимает тех, кто пропущен threshold раз."""
    rows = conn.execute("SELECT id, missed_scans FROM listings WHERE status != 'removed'").fetchall()
    removed = 0
    for row in rows:
        if row["id"] in seen_ids:
            continue
        missed = row["missed_scans"] + 1
        if missed >= threshold:
            conn.execute("UPDATE listings SET missed_scans = ?, status = 'removed' WHERE id = ?", (missed, row["id"]))
            removed += 1
        else:
            conn.execute("UPDATE listings SET missed_scans = ? WHERE id = ?", (missed, row["id"]))
    return removed


def count_not_removed(conn: sqlite3.Connection) -> int:
    """Сколько объявлений известно и не снято (active и filtered_out)."""
    return conn.execute("SELECT COUNT(*) FROM listings WHERE status != 'removed'").fetchone()[0]


def start_run(conn: sqlite3.Connection, now: datetime) -> int:
    cursor = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now.isoformat(),))
    return cursor.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, summary: RunSummary, *, now: datetime) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, search_complete = ?, ads_seen = ?, new = ?, changed = ?, "
        "price_changed = ?, removed = ?, errors = ? WHERE id = ?",
        (
            now.isoformat(),
            summary.status,
            summary.search_complete,
            summary.ads_seen,
            summary.new,
            summary.changed,
            summary.price_changed,
            summary.removed,
            json.dumps(summary.errors, ensure_ascii=False),
            run_id,
        ),
    )


def add_run_errors(conn: sqlite3.Connection, run_id: int, messages: list[str]) -> None:
    """Дописывает сообщения в JSON-список errors прогона; статус и счётчики не трогает."""
    if not messages:
        return
    row = conn.execute("SELECT errors FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Прогон {run_id} не найден")
    errors = [*json.loads(row["errors"]), *messages]
    conn.execute("UPDATE runs SET errors = ? WHERE id = ?", (json.dumps(errors, ensure_ascii=False), run_id))


def get_listing(conn: sqlite3.Connection, listing_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()


def get_price_history(conn: sqlite3.Connection, listing_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM price_history WHERE listing_id = ? ORDER BY observed_at, id", (listing_id,)
    ).fetchall()


def last_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()


def _content(listing: Listing) -> dict[str, object]:
    """Колонки listings, значения которых берутся из самого объявления."""
    return {
        "source": listing.source,
        "url": listing.url,
        "title": listing.title,
        "region": listing.region,
        "settlement": listing.settlement,
        "region_id": listing.region_id,
        "settlement_id": listing.settlement_id,
        "zone": listing.zone,
        "street": listing.street,
        "lat": listing.lat,
        "lon": listing.lon,
        "area_sot": listing.area_sot,
        "land_type": listing.land_type,
        "price_original": listing.price_original,
        "currency": listing.currency,
        "price_eur": listing.price_eur,
        "price_per_sot_eur": listing.price_per_sot_eur,
        "author_type": listing.author_type,
        "has_gas": listing.has_gas,
        "has_electricity": listing.has_electricity,
        "has_water": listing.has_water,
        "has_sewerage": listing.has_sewerage,
        "has_asphalt_road": listing.has_asphalt_road,
        "in_settlement": listing.in_settlement,
        "description": listing.description,
        "images": json.dumps(list(listing.images), ensure_ascii=False),
        "site_updated_at": listing.site_updated_at.isoformat() if listing.site_updated_at else None,
    }


def _last_price(conn: sqlite3.Connection, listing_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT price_original, currency FROM price_history WHERE listing_id = ? "
        "ORDER BY observed_at DESC, id DESC LIMIT 1",
        (listing_id,),
    ).fetchone()


def _record_price(conn: sqlite3.Connection, listing: Listing, ts: str) -> None:
    if listing.price_original is None:
        return
    conn.execute(
        "INSERT INTO price_history (listing_id, price_original, currency, price_eur, observed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (listing.id, listing.price_original, listing.currency, listing.price_eur, ts),
    )
