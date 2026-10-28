import pytest

from homealize.storage.db import connect, migrate


@pytest.fixture
def db():
    """Мигрированная база SQLite в памяти."""
    conn = connect(":memory:")
    migrate(conn)
    yield conn
    conn.close()
