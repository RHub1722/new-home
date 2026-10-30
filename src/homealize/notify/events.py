"""Что сообщить после прогона (спека §7.1, §13): новые участки, снижения цен, сводка первого прогона.

Функции только готовят сообщения; в очередь их ставит jobs.run_cycle.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.enrich.currency import to_eur
from homealize.notify import format as fmt
from homealize.notify.outbox import Outgoing
from homealize.storage import marks, rate_cache, repo
from homealize.storage.scores import load_scored

WINDOW = timedelta(days=1)  # окно «новых» не уходит в прошлое дальше суток


def plan_after_run(conn: sqlite3.Connection, settings: Settings, *, run_id: int, now: datetime) -> list[Outgoing]:
    since = repo.notify_window_start(conn, run_id, not_before=(now.astimezone(UTC) - WINDOW).isoformat())
    scored = load_scored(conn)
    favs = marks.marked(conn, marks.FAV)
    if not repo.has_listings_before(conn, since):
        return [_first_run(conn, settings, scored, favs)]
    return [
        *_new_listings(scored, settings, favs, since=since, run_id=run_id),
        *_price_drops(conn, scored, settings, favs, since=since),
    ]


def drop_share(before, after, rates: dict[str, float] | None) -> float | None:
    """На какую долю снизилась цена; в разных валютах — по евро. None — сравнить нельзя."""
    if before["currency"] == after["currency"]:
        old, new = before["price_original"], after["price_original"]
    else:
        old = to_eur(before["price_original"], before["currency"], rates)
        new = to_eur(after["price_original"], after["currency"], rates)
    if not old or new is None:
        return None
    return (old - new) / old


def _first_run(conn: sqlite3.Connection, settings: Settings, scored: list, favs: set[str]) -> Outgoing:
    loaded = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE status = 'active' AND filter_reason IS NULL"
    ).fetchone()[0]
    rated = [row for row in scored if row["rank"] is not None]
    lines = [
        f"✅ <b>Первый прогон</b>: загружено {loaded} {fmt.plural(loaded, 'участок', 'участка', 'участков')}, "
        f"в рейтинге {len(rated)}."
    ]
    top = rated[: settings.telegram.digest_top]
    if top:
        lines.append(f"Топ-{len(top)}:")
        lines += [fmt.listing_line(row, fav=row["listing_id"] in favs) for row in top]
    lines.append("Дальше буду присылать новые участки и снижения цен.")
    return Outgoing(key="first_run", kind="first_run", text="\n".join(lines))


def _new_listings(scored: list, settings: Settings, favs: set[str], *, since: str, run_id: int) -> list[Outgoing]:
    t = settings.telegram
    known_groups = {row["duplicate_group"] for row in scored if row["duplicate_group"] and row["first_seen_at"] < since}
    fresh = [
        row
        for row in scored  # load_scored уже отсортирован по месту
        if row["rank"] is not None
        and row["first_seen_at"] >= since
        and (row["total"] >= t.new_min_total or row["rank"] <= t.new_max_rank)
        and row["duplicate_group"] not in known_groups
    ]
    items = [
        Outgoing(
            key=f"new:{row['listing_id']}",
            kind="new",
            text=fmt.new_listing(row, fav=row["listing_id"] in favs),
            listing_id=row["listing_id"],
            preview_url=fmt.first_photo(row),
        )
        for row in fresh[: t.max_new_per_run]
    ]
    rest = len(fresh) - t.max_new_per_run
    if rest > 0:
        word = fmt.plural(rest, "новый участок", "новых участка", "новых участков")
        items.append(Outgoing(key=f"new-more:{run_id}", kind="new", text=f"…и ещё {rest} {word} — /new"))
    return items


def _price_drops(
    conn: sqlite3.Connection, scored: list, settings: Settings, favs: set[str], *, since: str
) -> list[Outgoing]:
    t = settings.telegram
    changed = {
        row[0] for row in conn.execute("SELECT DISTINCT listing_id FROM price_history WHERE observed_at >= ?", (since,))
    }
    latest = rate_cache.latest_rates(conn)
    rates = latest[1] if latest else None
    items = []
    for row in scored:
        if row["listing_id"] not in changed or row["rank"] is None or row["total"] < t.price_drop_min_total:
            continue
        history = repo.get_price_history(conn, row["listing_id"])
        if len(history) < 2 or history[-1]["observed_at"] < since:
            continue
        before, after = history[-2], history[-1]
        share = drop_share(before, after, rates)
        if share is None or share < t.price_drop_min_share:
            continue
        items.append(
            Outgoing(
                key=f"price_drop:{row['listing_id']}:{after['id']}",
                kind="price_drop",
                text=fmt.price_drop(
                    row,
                    old=fmt.money(before["price_original"], before["currency"]),
                    new=fmt.money(after["price_original"], after["currency"]),
                    percent=round(share * 100),
                    fav=row["listing_id"] in favs,
                ),
                listing_id=row["listing_id"],
                preview_url=fmt.first_photo(row),
            )
        )
    return items
