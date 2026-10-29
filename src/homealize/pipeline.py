"""Один прогон: обход источника, фильтры, сохранение, история цен, снятие пропавших."""

import logging
import sqlite3
from datetime import datetime

from homealize.config import Settings
from homealize.filters import filter_reason
from homealize.models import RunSummary
from homealize.sources.base import Source, SourceError
from homealize.storage import repo
from homealize.storage.repo import UpsertOutcome

log = logging.getLogger(__name__)

MAX_UNPARSED_SHARE = 0.2  # больше этой доли нераспознанных объявлений: обход не считается надёжным
MIN_SEEN_SHARE = 0.5  # обход, увидевший меньше этой доли известных объявлений, ничего не снимает


def run_once(conn: sqlite3.Connection, source: Source, settings: Settings, *, now: datetime) -> RunSummary:
    summary = RunSummary()
    run_id = repo.start_run(conn, now)
    conn.commit()
    seen: set[str] = set()
    unparsed_ids: set[str] = set()  # нераспознанные объявления: не сохраняем, но и пропавшими не считаем
    unparsed = received = 0
    try:
        for page in source.iter_pages():
            summary.errors.extend(page.parse_errors)
            unparsed_ids.update(page.unparsed_ids)
            unparsed += len(page.parse_errors)
            received += len(page.listings) + len(page.parse_errors)
            for listing in page.listings:
                if listing.id in seen:
                    continue
                seen.add(listing.id)
                reason = filter_reason(listing, settings.filters)
                outcome = repo.upsert_listing(
                    conn,
                    listing,
                    status="filtered_out" if reason else "active",
                    filter_reason=reason,
                    now=now,
                )
                _count(summary, outcome)
            conn.commit()
        problems = _removal_problems(conn, seen | unparsed_ids, unparsed=unparsed, received=received)
        if problems:
            for message in problems:
                log.error(message)
            summary.errors.extend(problems)
        else:
            summary.removed = repo.mark_unseen(
                conn, seen | unparsed_ids, threshold=settings.lifecycle.removal_missed_scans
            )
            summary.search_complete = True
    except SourceError as exc:
        conn.rollback()
        log.error("Обход %s прерван: %s", source.name, exc)
        summary.errors.append(f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        conn.rollback()
        log.exception("Прогон %s упал", run_id)
        summary.errors.append(f"{type(exc).__name__}: {exc}")
        _finish(conn, run_id, summary, seen, now)
        raise

    _finish(conn, run_id, summary, seen, now)
    log.info(
        "Прогон %s завершён (%s): увидено %s, новых %s, цена изменилась %s, обновлено %s, снято %s, ошибок %s",
        run_id, summary.status, summary.ads_seen, summary.new, summary.price_changed,
        summary.changed, summary.removed, len(summary.errors),
    )
    return summary


def _removal_problems(
    conn: sqlite3.Connection, visible: set[str], *, unparsed: int, received: int
) -> list[str]:
    """Причины, по которым обходу нельзя доверять и снимать пропавшие объявления нельзя."""
    problems = []
    if received > 0 and unparsed / received > MAX_UNPARSED_SHARE:
        problems.append(f"Слишком много нераспознанных объявлений: {unparsed} из {received} — снятие пропущено")
    known = repo.count_not_removed(conn)
    if known > 0 and len(visible) < MIN_SEEN_SHARE * known:
        problems.append(f"Обход увидел {len(visible)} объявлений при {known} известных — снятие пропущено")
    return problems


def _count(summary: RunSummary, outcome: UpsertOutcome) -> None:
    if outcome is UpsertOutcome.NEW:
        summary.new += 1
    elif outcome is UpsertOutcome.PRICE_CHANGED:
        summary.price_changed += 1
    elif outcome is UpsertOutcome.UPDATED:
        summary.changed += 1


def _finish(conn: sqlite3.Connection, run_id: int, summary: RunSummary, seen: set[str], now: datetime) -> None:
    summary.ads_seen = len(seen)
    repo.finish_run(conn, run_id, summary, now=now)
    conn.commit()
