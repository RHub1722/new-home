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


def replace_scores(conn: sqlite3.Connection, records: list[dict[str, object]]) -> None:
    conn.execute("DELETE FROM scores")
    if records:
        conn.executemany(
            f"INSERT INTO scores ({', '.join(SCORE_COLUMNS)}) VALUES ({', '.join('?' * len(SCORE_COLUMNS))})",
            [[record[column] for column in SCORE_COLUMNS] for record in records],
        )


def load_scored(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Оценённые объявления вместе с полями объявления: сначала рейтинг по месту, потом остальные."""
    return conn.execute(
        "SELECT s.*, l.title, l.url, l.region, l.settlement, l.zone, l.area_sot, l.price_original, l.currency, "
        "l.author_type, l.first_seen_at "
        "FROM scores s JOIN listings l ON l.id = s.listing_id "
        "ORDER BY s.rank IS NULL, s.rank, s.total DESC, s.listing_id"
    ).fetchall()
