import logging
from pathlib import Path

import pytest

from homealize import cli
from homealize.config import Settings
from homealize.logging_setup import configure_logging, reset_logging
from homealize.models import Poi
from homealize.scoring.engine import ScoreSummary
from tests.factories import FakeSource, make_listing, page


@pytest.fixture(autouse=True)
def _clean_logging():
    yield
    reset_logging()


def write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"storage:\n  db_path: '{(tmp_path / 'data' / 'test.db').as_posix()}'\n"
        f"logging:\n  path: '{(tmp_path / 'logs' / 'test.log').as_posix()}'\n"
        f"export:\n  dir: '{(tmp_path / 'exports').as_posix()}'\n",
        encoding="utf-8",
    )
    return cfg


@pytest.fixture(autouse=True)
def _fake_evaluation(monkeypatch):
    calls = []

    def fake_evaluate(conn, settings, http, *, now, force_poi=False, sleep=None):
        calls.append(force_poi)
        return ScoreSummary(scored=2, eligible=1, best_total=71.5, warnings=["тестовое предупреждение"])

    monkeypatch.setattr(cli, "evaluate", fake_evaluate)
    return calls


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


def test_run_evaluates_and_writes_excel(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))
    assert cli.main(["--config", str(cfg), "run"]) == 0
    out = capsys.readouterr().out
    assert "Оценка: оценено 2, в рейтинге 1, лучший балл 72" in out
    assert "Предупреждение: тестовое предупреждение" in out
    assert (tmp_path / "exports" / "latest.xlsx").exists()


def test_run_returns_3_when_evaluation_fails(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))

    def broken(*args, **kwargs):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(cli, "evaluate", broken)
    assert cli.main(["--config", str(cfg), "run"]) == 3
    assert "Оценка не выполнена: RuntimeError: сломалось" in capsys.readouterr().err


def test_rescore_does_not_scan(tmp_path, capsys, monkeypatch, _fake_evaluation):
    cfg = write_config(tmp_path)

    def no_source(settings):
        raise AssertionError("rescore не должен обходить сайт")

    monkeypatch.setattr(cli, "build_nine99_source", no_source)
    assert cli.main(["--config", str(cfg), "rescore"]) == 0
    assert _fake_evaluation == [False]
    assert "Оценка:" in capsys.readouterr().out


def test_export_writes_excel(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "export"]) == 0
    assert (tmp_path / "exports" / "latest.xlsx").exists()
    assert "Excel:" in capsys.readouterr().out


def test_refresh_poi(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    pois = [Poi("node/1", "school", None, None, 47.0, 28.8), Poi("node/2", "pharmacy", None, None, 47.0, 28.8)]
    monkeypatch.setattr(cli, "ensure_pois", lambda conn, http, settings, *, now, force: (pois, None))
    assert cli.main(["--config", str(cfg), "refresh-poi"]) == 0
    assert "Инфраструктура обновлена: 2 объектов" in capsys.readouterr().out


def test_refresh_poi_failure_returns_3(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "ensure_pois",
                        lambda conn, http, settings, *, now, force: ([], "POI не обновлены (HTTPError: 503)"))
    assert cli.main(["--config", str(cfg), "refresh-poi"]) == 3
    assert "POI не обновлены" in capsys.readouterr().err


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
