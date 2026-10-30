"""Один цикл прогона (спека §4 «Поток одного прогона», §13): обход 999.md → оценка и Excel → уведомления.

Им пользуются и `homealize run`, и расписание `serve`. Весь цикл идёт под блокировкой прогона.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from homealize.config import Settings
from homealize.evaluate import build_http_client, evaluate
from homealize.export.excel import ExportError, export_workbook
from homealize.models import RunSummary
from homealize.notify.alerts import Failure, plan_alerts
from homealize.notify.digest import plan_digest
from homealize.notify.events import plan_after_run
from homealize.notify.outbox import Delivery, Notifier, Outgoing, deliver, enqueue
from homealize.pipeline import run_once
from homealize.runlock import LockBusyError, hold_lock
from homealize.scoring.engine import ScoreSummary
from homealize.sources.base import Source
from homealize.storage import repo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evaluation:
    """Итог оценки и выгрузки: что удалось и что дописать в журнал прогона."""

    summary: ScoreSummary | None  # None — оценка упала
    path: Path | None  # None — Excel не сохранён
    messages: list[str]
    failure: Failure | None

    @property
    def ok(self) -> bool:
        return self.failure is None


@dataclass
class CycleResult:
    scan: RunSummary | None  # None — обход упал, не дойдя до итогов
    run_id: int | None  # None — прогон даже не начался
    evaluation: Evaluation | None  # None — оценка не запускалась
    planned: list[Outgoing] = field(default_factory=list)
    delivery: Delivery | None = None  # None — ничего не отправлялось (--dry-run или нет Telegram)
    crash: str | None = None


def evaluate_and_export(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    now: datetime,
    http_factory: Callable[[], httpx.Client] = build_http_client,
    sleep: Callable[[float], None] = time.sleep,
) -> Evaluation:
    """Оценка и Excel. Ошибки наружу не выходят: обход к этому моменту уже сохранён."""
    try:
        with http_factory() as http:
            result = evaluate(conn, settings, http, now=now, sleep=sleep)
        try:
            path = export_workbook(conn, settings.export, now=now)
        except ExportError as exc:
            message = f"Excel не сохранён: {exc}"
            return Evaluation(result, None, [*result.warnings, message], Failure("export_failed", message))
    except Exception as exc:  # оценка не должна терять уже сохранённый обход
        conn.rollback()  # недописанное оценкой не должно попасть в базу вместе с записью в журнал прогона
        log.exception("Оценка не выполнена")
        message = f"Оценка не выполнена: {type(exc).__name__}: {exc}"
        return Evaluation(None, None, [message], Failure("evaluation_failed", message))
    return Evaluation(result, path, list(result.warnings), None)


def run_cycle(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    source_factory: Callable[[], Source],
    notifier: Notifier | None,
    now: datetime,
    clock: Callable[[], datetime],
    dry_run: bool = False,
    http_factory: Callable[[], httpx.Client] = build_http_client,
    sleep: Callable[[float], None] = time.sleep,
) -> CycleResult:
    """Обход, оценка, Excel и уведомления. LockBusyError — если уже идёт другой прогон."""
    with hold_lock(settings.storage.run_lock_path):
        stale = repo.close_stale_runs(conn)
        conn.commit()
        if stale:
            log.warning("Закрыто зависших прогонов: %s", stale)
        before = repo.last_run(conn)
        scan, crash = _scan(conn, settings, source_factory, now=now, clock=clock)
        after = repo.last_run(conn)
        run_id = after["id"] if after is not None and (before is None or after["id"] != before["id"]) else None

        evaluation = None
        if crash is None:
            evaluation = evaluate_and_export(conn, settings, now=now, http_factory=http_factory, sleep=sleep)
            if run_id is not None and evaluation.messages:
                repo.add_run_errors(conn, run_id, evaluation.messages)
                conn.commit()

        planned = plan_alerts(conn, settings, _failures(scan, crash, evaluation), run_id=run_id, now=now)
        scored = run_id is not None and evaluation is not None and evaluation.summary is not None
        if scored:
            planned += plan_after_run(conn, settings, run_id=run_id, now=now)
        result = CycleResult(scan, run_id, evaluation, planned, crash=crash)
        if dry_run or notifier is None:
            return result  # окно «новых» остаётся открытым: они придут со следующим прогоном
        enqueue(conn, planned, now=now)
        if scored:
            repo.mark_notified(conn, run_id, now=now)
        conn.commit()
        result.delivery = deliver(conn, notifier, settings.telegram, now=now, sleep=sleep)
        return result


def send_digest(
    conn: sqlite3.Connection,
    settings: Settings,
    notifier: Notifier | None,
    *,
    now: datetime,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Outgoing, bool, Delivery | None]:
    """Дайджест дня: сообщение, поставлено ли оно в очередь (False — за сегодня уже был) и итог отправки.

    Итог None — ничего не отправлялось: --dry-run, нет Telegram или идёт другой прогон (блокировка занята).
    В последнем случае дайджест уже стоит в очереди и уйдёт со следующей отправкой.
    """
    item = plan_digest(conn, settings, now=now)
    if dry_run or notifier is None:
        return item, False, None
    added = enqueue(conn, [item], now=now) > 0
    conn.commit()  # дайджест дня не теряется, даже если отправить его сейчас нельзя
    try:
        with hold_lock(settings.storage.run_lock_path):  # очередь отправляет один процесс за раз
            delivery = deliver(conn, notifier, settings.telegram, now=now, sleep=sleep)
    except LockBusyError:
        return item, added, None
    return item, added, delivery


def _scan(
    conn: sqlite3.Connection,
    settings: Settings,
    source_factory: Callable[[], Source],
    *,
    now: datetime,
    clock: Callable[[], datetime],
) -> tuple[RunSummary | None, str | None]:
    """(итог обхода, None) или (None, текст сбоя), если упал сам обход или создание источника."""
    try:
        source = source_factory()
    except Exception as exc:
        log.exception("Источник объявлений не создан")
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return run_once(conn, source, settings, now=now, clock=clock), None
    except Exception as exc:  # run_once уже записал прогон как failed и откатил недописанное
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        source.close()


def _failures(scan: RunSummary | None, crash: str | None, evaluation: Evaluation | None) -> list[Failure]:
    failures = []
    if crash is not None:
        failures.append(Failure("crashed", crash))
    elif scan is not None and scan.failure is not None:
        failures.append(Failure(scan.failure, scan.failure_detail))
    if evaluation is not None and evaluation.failure is not None:
        failures.append(evaluation.failure)
    return failures
