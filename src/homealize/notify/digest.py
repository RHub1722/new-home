"""Дайджест дня (спека §7.1): новые за сутки, топ со сдвигами мест, кто выпал из топа и почему."""

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from html import escape

from homealize.config import Settings
from homealize.notify import format as fmt
from homealize.notify.outbox import Outgoing
from homealize.scoring.components import EXCLUSION_LABELS
from homealize.storage import marks, repo
from homealize.storage.scores import load_scored


def digest_key(day: date) -> str:
    return f"digest:{day.isoformat()}"


def plan_digest(conn: sqlite3.Connection, settings: Settings, *, now: datetime) -> Outgoing:
    day = now.astimezone(settings.schedule.zone).date()
    key = digest_key(day)
    scored = load_scored(conn)
    by_id = {row["listing_id"]: row for row in scored}
    top = [row for row in scored if row["rank"] is not None][: settings.telegram.digest_top]
    favs = marks.marked(conn, marks.FAV)
    hidden = marks.marked(conn, marks.HIDDEN)
    previous = previous_top(conn, exclude_key=key)
    new_total, new_rated = _new_counts(conn, by_id, now=now)

    lines = [f"📋 <b>Дайджест за {day:%d.%m}</b>", f"Новых за сутки: {new_total}, из них в рейтинге {new_rated}."]
    if top:
        before = {entry["id"]: entry["rank"] for entry in previous or []}
        lines += ["", f"Топ-{len(top)}:"]
        for row in top:
            note = _movement(before.get(row["listing_id"]), row["rank"]) if previous is not None else ""
            lines.append(fmt.listing_line(row, fav=row["listing_id"] in favs, note=note))
    else:
        lines.append("В рейтинге пока пусто.")
    current = {row["listing_id"] for row in top}
    dropped = [entry for entry in previous or [] if entry["id"] not in current]
    if dropped:
        lines += ["", "Выпали из топа:"]
        lines += [f"• {_dropped_line(conn, entry, by_id.get(entry['id']), hidden)}" for entry in dropped]
    snapshot = [
        {
            "id": row["listing_id"],
            "rank": row["rank"],
            "price_eur": row["price_eur"],
            "settlement": row["settlement"],
            "area_sot": row["area_sot"],
        }
        for row in top
    ]
    return Outgoing(key=key, kind="digest", topic=day.isoformat(), text="\n".join(lines), data={"top": snapshot})


def previous_top(conn: sqlite3.Connection, *, exclude_key: str) -> list[dict] | None:
    """Топ из последнего прошлого дайджеста (с ним сравниваются места)."""
    row = conn.execute(
        "SELECT payload FROM notifications WHERE kind = 'digest' AND key != ? ORDER BY id DESC LIMIT 1",
        (exclude_key,),
    ).fetchone()
    if row is None:
        return None
    top = json.loads(row["payload"]).get("top")
    return top if isinstance(top, list) else None


def _new_counts(conn: sqlite3.Connection, by_id: dict, *, now: datetime) -> tuple[int, int]:
    since = (now.astimezone(UTC) - timedelta(days=1)).isoformat()
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM listings WHERE first_seen_at >= ? AND filter_reason IS NULL", (since,)
        )
    ]
    rated = sum(1 for listing_id in ids if listing_id in by_id and by_id[listing_id]["rank"] is not None)
    return len(ids), rated


def _movement(before: int | None, rank: int) -> str:
    if before is None:
        return "🆕 в топе"
    if before == rank:
        return "="
    return f"↑{before - rank}" if rank < before else f"↓{rank - before}"


def _dropped_line(conn: sqlite3.Connection, entry: dict, row, hidden: set[str]) -> str:
    name = escape(entry.get("settlement") or fmt.NO_SETTLEMENT)
    label = f"{name}, {fmt.area(entry.get('area_sot'))} (было {entry['rank']}-е место)"
    return f"{label} — {_drop_reason(conn, entry, row, hidden)} {fmt.ad_command(entry['id'])}"


def _drop_reason(conn: sqlite3.Connection, entry: dict, row, hidden: set[str]) -> str:
    listing = repo.get_listing(conn, entry["id"])
    if listing is None or listing["status"] == "removed":
        return "снято с сайта"
    if listing["status"] == "filtered_out":
        return "отсеяно фильтром"
    if entry["id"] in hidden:
        return "скрыто вами"
    if row is None:
        return "нет оценки"
    old_price, reason = entry.get("price_eur"), row["excluded_reason"]
    if old_price and row["price_eur"] and row["price_eur"] > old_price:
        text = f"подорожал: {fmt.money(old_price)} → {fmt.money(row['price_eur'])}"
        return f"{text} ({EXCLUSION_LABELS.get(reason, reason)})" if reason else text
    if reason:
        return EXCLUSION_LABELS.get(reason, reason)
    if row["rank"] is not None:
        return f"опустился на {row['rank']}-е место"
    return "вне рейтинга"
