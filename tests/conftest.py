import pytest

from homealize.storage.db import connect, migrate


@pytest.fixture
def db():
    """Мигрированная база SQLite в памяти."""
    conn = connect(":memory:")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    """Тесты никогда не берут настоящий токен бота из окружения."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
