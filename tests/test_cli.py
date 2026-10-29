from pathlib import Path

import pytest

from homealize import cli
from homealize.logging_setup import reset_logging
from tests.factories import FakeSource, make_listing, page


@pytest.fixture(autouse=True)
def _clean_logging():
    yield
    reset_logging()


def write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"storage:\n  db_path: '{(tmp_path / 'data' / 'test.db').as_posix()}'\n"
        f"logging:\n  path: '{(tmp_path / 'logs' / 'test.log').as_posix()}'\n",
        encoding="utf-8",
    )
    return cfg


def test_init_db_creates_database(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "init-db"]) == 0
    assert (tmp_path / "data" / "test.db").exists()
    assert "схема v1" in capsys.readouterr().out


def test_run_saves_listings_and_prints_summary(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    fake = FakeSource([page(make_listing(id="a"), make_listing(id="b", region="Кагул"))])
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: fake)
    assert cli.main(["--config", str(cfg), "run"]) == 0
    out = capsys.readouterr().out
    assert "увидено 2" in out
    assert "новых 2" in out
    assert fake.closed
    reset_logging()
    assert "Прогон" in (tmp_path / "logs" / "test.log").read_text(encoding="utf-8")


def test_run_returns_1_when_scan_interrupted(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([], fail_at=0))
    assert cli.main(["--config", str(cfg), "run"]) == 1
    assert "НЕ завершён" in capsys.readouterr().out
