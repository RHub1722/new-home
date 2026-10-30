import logging
from pathlib import Path

import pytest

from homealize import cli
from homealize.config import Settings
from homealize.logging_setup import configure_logging, reset_logging
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
    assert "схема v2" in capsys.readouterr().out


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


def test_missing_config_prints_russian_error(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "nope.yaml"), "run"]) == 2
    err = capsys.readouterr().err
    assert "Не удалось прочитать настройки" in err


def test_invalid_config_prints_russian_error(tmp_path, capsys):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("filters:\n  area_max: 12\n", encoding="utf-8")
    assert cli.main(["--config", str(cfg), "init-db"]) == 2
    err = capsys.readouterr().err
    assert "Не удалось прочитать настройки" in err


def test_settings_file_in_wrong_encoding_prints_russian_error(tmp_path, capsys):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text('filters:\n  land_type: "Участок"\n', encoding="cp1251")
    assert cli.main(["--config", str(cfg), "init-db"]) == 2
    err = capsys.readouterr().err
    assert "Не удалось прочитать настройки" in err


def test_configure_logging_silences_httpx_request_lines(tmp_path):
    cfg = Settings.model_validate({"logging": {"path": (tmp_path / "logs" / "x.log").as_posix()}}).logging
    configure_logging(cfg)
    assert logging.getLogger("httpx").level == logging.WARNING
    reset_logging()
    assert logging.getLogger("httpx").level == logging.NOTSET
