"""Подключение к SQLite и применение миграций."""

import sqlite3
from importlib import resources
from pathlib import Path

MIGRATIONS_PACKAGE = "homealize.storage.migrations"
IN_MEMORY = ":memory:"


def connect(path: str | Path) -> sqlite3.Connection:
    """Открывает базу; для файла создаёт папку и включает WAL, чтобы дашборд мог читать во время прогона."""
    in_memory = str(path) == IN_MEMORY
    if not in_memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Применяет недостающие миграции по порядку, каждую атомарно. Возвращает версию схемы."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in _migrations():
        if version <= current:
            continue
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
        current = version
    return current


def _migrations() -> list[tuple[int, str]]:
    found = []
    for entry in resources.files(MIGRATIONS_PACKAGE).iterdir():
        if entry.name.endswith(".sql"):
            version = int(entry.name.split("_", 1)[0])
            found.append((version, entry.read_text(encoding="utf-8")))
    return sorted(found)
