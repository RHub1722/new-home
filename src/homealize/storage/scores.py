"""Снимок оценки: перезаписывается целиком при каждом пересчёте. Функции не делают commit."""

import sqlite3

SCORE_COLUMNS = (
    "listing_id",
    "computed_at",
    "price_eur",
    "price_per_sot_eur",
    "lat",
    "lon",
    "coords_approx",
    "route_km",
    "route_min",
    "nearest_entry",
    "poi_nearest_m",
    "market_median_eur_per_sot",
    "median_level",
    "median_n",
    "duplicate_group",
    "is_group_primary",
    "components",
    "explanations",
    "flags",
    "total",
    "excluded_reason",
    "rank",
)
_SCORED = (
    "SELECT s.*, l.title, l.url, l.region, l.settlement, l.settlement_id, l.zone, l.area_sot, l.price_original, "
    "l.currency, l.author_type, l.first_seen_at, l.site_updated_at, l.images, l.description, "
    "l.has_electricity, l.has_gas, l.has_water, l.has_sewerage "
    "FROM scores s JOIN listings l ON l.id = s.listing_id"
)


def replace_scores(conn: sqlite3.Connection, records: list[dict[str, object]]) -> None:
    conn.execute("DELETE FROM scores")
    if records:
        conn.executemany(
            f"INSERT INTO scores ({', '.join(SCORE_COLUMNS)}) VALUES ({', '.join('?' * len(SCORE_COLUMNS))})",
            [[record[column] for column in SCORE_COLUMNS] for record in records],
        )


def assign_ranks(conn: sqlite3.Connection) -> None:
    """Места 1, 2, … у объявлений без причины исключения: выше балл, при равенстве дешевле, потом по id."""
    conn.execute("UPDATE scores SET rank = NULL")
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT listing_id FROM scores WHERE excluded_reason IS NULL ORDER BY total DESC, price_eur, listing_id"
        ).fetchall()
    ]
    conn.executemany(
        "UPDATE scores SET rank = ? WHERE listing_id = ?",
        [(rank, listing_id) for rank, listing_id in enumerate(ids, start=1)],
    )


def apply_hidden(conn: sqlite3.Connection, hidden: set[str]) -> None:
    """После /hide и /unhide: причина hidden ставится или снимается без пересчёта баллов, места — заново.

    Скрытым становится только объявление без другой причины исключения: «дороже бюджета» важнее.
    """
    for row in conn.execute("SELECT listing_id, excluded_reason FROM scores").fetchall():
        if row["excluded_reason"] is None and row["listing_id"] in hidden:
            conn.execute("UPDATE scores SET excluded_reason = 'hidden' WHERE listing_id = ?", (row["listing_id"],))
        elif row["excluded_reason"] == "hidden" and row["listing_id"] not in hidden:
            conn.execute("UPDATE scores SET excluded_reason = NULL WHERE listing_id = ?", (row["listing_id"],))
    assign_ranks(conn)


def load_scored(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Оценённые объявления вместе с полями объявления: сначала рейтинг по месту, потом остальные."""
    return conn.execute(f"{_SCORED} ORDER BY s.rank IS NULL, s.rank, s.total DESC, s.listing_id").fetchall()


def get_scored(conn: sqlite3.Connection, listing_id: str) -> sqlite3.Row | None:
    return conn.execute(f"{_SCORED} WHERE s.listing_id = ?", (listing_id,)).fetchone()
