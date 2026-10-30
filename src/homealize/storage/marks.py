"""Отметки пользователя из бота: ★ избранное и скрытые объявления. Функции не делают commit."""

import sqlite3
from datetime import UTC, datetime

FAV = "fav"
HIDDEN = "hidden"


def add_mark(conn: sqlite3.Connection, listing_id: str, mark: str, *, now: datetime) -> bool:
    """True, если отметки не было и она поставлена. Объявление должно быть в listings."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO user_marks (listing_id, mark, created_at) VALUES (?, ?, ?)",
        (listing_id, mark, now.astimezone(UTC).isoformat()),
    )
    return cursor.rowcount == 1


def remove_mark(conn: sqlite3.Connection, listing_id: str, mark: str) -> bool:
    """True, если отметка была и снята."""
    cursor = conn.execute("DELETE FROM user_marks WHERE listing_id = ? AND mark = ?", (listing_id, mark))
    return cursor.rowcount == 1


def marked(conn: sqlite3.Connection, mark: str) -> set[str]:
    return {row[0] for row in conn.execute("SELECT listing_id FROM user_marks WHERE mark = ?", (mark,))}
