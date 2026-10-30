"""Курсы валют BNM по дням (леев за единицу валюты). Функции не делают commit."""

import sqlite3


def get_rates(conn: sqlite3.Connection, day: str) -> dict[str, float] | None:
    rows = conn.execute("SELECT currency, mdl_per_unit FROM fx_rates WHERE rate_date = ?", (day,)).fetchall()
    return {row["currency"]: row["mdl_per_unit"] for row in rows} or None


def put_rates(conn: sqlite3.Connection, day: str, rates: dict[str, float]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO fx_rates (rate_date, currency, mdl_per_unit) VALUES (?, ?, ?)",
        [(day, currency, value) for currency, value in rates.items()],
    )


def latest_rates(conn: sqlite3.Connection) -> tuple[str, dict[str, float]] | None:
    day = conn.execute("SELECT MAX(rate_date) FROM fx_rates").fetchone()[0]
    if day is None:
        return None
    return day, get_rates(conn, day) or {}
