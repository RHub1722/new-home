import sqlite3

import pytest

from homealize.storage.db import connect, migrate


def tables(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_migrate_creates_schema_v1():
    conn = connect(":memory:")
    assert migrate(conn) == 1
    assert {"listings", "price_history", "runs"} <= tables(conn)


def test_migrate_twice_is_noop():
    conn = connect(":memory:")
    migrate(conn)
    assert migrate(conn) == 1


def test_file_database_uses_wal_and_creates_folder(tmp_path):
    path = tmp_path / "nested" / "homealize.db"
    conn = connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
    assert path.exists()


def test_listing_status_must_be_known(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO listings (id, source, url, title, author_type, first_seen_at, last_seen_at, status) "
            "VALUES ('x', '999', 'u', 't', 'private', 'now', 'now', 'bogus')"
        )


def test_price_history_requires_existing_listing(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO price_history (listing_id, observed_at) VALUES ('missing', 'now')")


def test_failed_migration_is_rolled_back(monkeypatch):
    monkeypatch.setattr(
        "homealize.storage.db._migrations",
        lambda: [(1, "CREATE TABLE t_ok (x INTEGER);\nCREATE TABLE t_ok (x INTEGER);")],
    )
    conn = connect(":memory:")
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn)
        assert not conn.in_transaction
        assert "t_ok" not in tables(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()
