import json
import logging
import os
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

from homealize import cli, jobs
from homealize.config import LoggingSettings, Settings
from homealize.dashboard import APP_PATH, CONFIG_ENV
from homealize.export.excel import ExportError
from homealize.logging_setup import configure_logging, reset_logging
from homealize.models import Poi
from homealize.notify.telegram import TelegramError
from homealize.runlock import hold_lock
from homealize.scoring.engine import ScoreSummary
from homealize.storage import repo
from homealize.storage.db import connect
from tests.factories import FakeSource, FakeTelegram, make_listing, page


@pytest.fixture(autouse=True)
def _clean_logging():
    yield
    reset_logging()


def write_config(tmp_path: Path, *, env: str | None = None) -> Path:
    """Настройки теста: все пути во временной папке, .env — только если передан env."""
    data = tmp_path / "data"
    if env is not None:
        (tmp_path / ".env").write_text(env, encoding="utf-8")
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"storage:\n  db_path: '{(data / 'test.db').as_posix()}'\n"
        f"  run_lock_path: '{(data / 'run.lock').as_posix()}'\n"
        f"  serve_lock_path: '{(data / 'serve.lock').as_posix()}'\n"
        f"logging:\n  path: '{(tmp_path / 'logs' / 'test.log').as_posix()}'\n"
        f"export:\n  dir: '{(tmp_path / 'exports').as_posix()}'\n"
        f"telegram:\n  env_file: '{(tmp_path / '.env').as_posix()}'\n",
        encoding="utf-8",
    )
    return cfg


def last_run_and_listing(tmp_path: Path, listing_id: str = "a"):
    """Последний прогон и объявление из базы теста, открытой заново после main()."""
    conn = connect(tmp_path / "data" / "test.db")
    try:
        return repo.last_run(conn), repo.get_listing(conn, listing_id)
    finally:
        conn.close()


def run_errors(tmp_path: Path) -> list[str]:
    run, _ = last_run_and_listing(tmp_path)
    return json.loads(run["errors"])


@pytest.fixture(autouse=True)
def _fake_evaluation(monkeypatch):
    calls = []

    def fake_evaluate(conn, settings, http, *, now, force_poi=False, sleep=None):
        calls.append(force_poi)
        return ScoreSummary(scored=2, eligible=1, best_total=71.5, warnings=["тестовое предупреждение"])

    monkeypatch.setattr(jobs, "evaluate", fake_evaluate)
    return calls


def test_init_db_creates_database(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "init-db"]) == 0
    assert (tmp_path / "data" / "test.db").exists()
    assert "схема v3" in capsys.readouterr().out


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

    monkeypatch.setattr(jobs, "evaluate", broken)
    assert cli.main(["--config", str(cfg), "run"]) == 3
    failure = "Оценка не выполнена: RuntimeError: сломалось"
    assert failure in capsys.readouterr().err
    run, listing = last_run_and_listing(tmp_path)
    assert listing is not None  # обход сохранён
    assert run["status"] == "ok"  # статус описывает только обход
    assert failure in json.loads(run["errors"])


def test_failed_evaluation_does_not_commit_its_half_done_writes(tmp_path, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))

    def half_done(conn, *args, **kwargs):
        conn.execute("UPDATE listings SET title = 'недописано'")  # незавершённая запись оценки
        raise RuntimeError("сломалось")

    monkeypatch.setattr(jobs, "evaluate", half_done)
    assert cli.main(["--config", str(cfg), "run"]) == 3
    run, listing = last_run_and_listing(tmp_path)
    assert listing["title"] == make_listing().title
    assert "Оценка не выполнена: RuntimeError: сломалось" in json.loads(run["errors"])


def test_run_returns_3_when_excel_fails_but_keeps_summary_and_warnings(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))

    def locked(conn, settings, *, now):
        raise ExportError("файл открыт в другой программе")

    monkeypatch.setattr(jobs, "export_workbook", locked)
    assert cli.main(["--config", str(cfg), "run"]) == 3
    captured = capsys.readouterr()
    assert "Оценка: оценено 2, в рейтинге 1, лучший балл 72." in captured.out
    assert "Excel" not in captured.out
    assert "Предупреждение: тестовое предупреждение" in captured.out
    assert "Excel не сохранён: файл открыт в другой программе" in captured.err
    run, _ = last_run_and_listing(tmp_path)
    assert run["status"] == "ok"
    assert json.loads(run["errors"]) == ["тестовое предупреждение", "Excel не сохранён: файл открыт в другой программе"]


def test_run_warnings_are_appended_to_the_scan_errors(tmp_path, monkeypatch):
    cfg = write_config(tmp_path)
    listings = [make_listing(id=str(i)) for i in range(5)]
    source = FakeSource([page(*listings, errors=("объявление 9: ошибка разбора",))])
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: source)
    assert cli.main(["--config", str(cfg), "run"]) == 0
    run, _ = last_run_and_listing(tmp_path)
    assert run["status"] == "ok"
    assert json.loads(run["errors"]) == ["объявление 9: ошибка разбора", "тестовое предупреждение"]


def test_run_without_warnings_leaves_errors_empty(tmp_path, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))
    monkeypatch.setattr(jobs, "evaluate", lambda *args, **kwargs: ScoreSummary(scored=1, eligible=1, best_total=60.0))
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert run_errors(tmp_path) == []


def test_rescore_does_not_scan(tmp_path, capsys, monkeypatch, _fake_evaluation):
    cfg = write_config(tmp_path)

    def no_source(settings):
        raise AssertionError("rescore не должен обходить сайт")

    monkeypatch.setattr(cli, "build_nine99_source", no_source)
    assert cli.main(["--config", str(cfg), "rescore"]) == 0
    assert _fake_evaluation == [False]
    assert "Оценка:" in capsys.readouterr().out


def test_rescore_does_not_write_to_the_run_log(tmp_path, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert cli.main(["--config", str(cfg), "rescore"]) == 0
    assert run_errors(tmp_path) == ["тестовое предупреждение"]


def test_rescore_returns_3_when_evaluation_fails(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(jobs, "evaluate", broken)
    assert cli.main(["--config", str(cfg), "rescore"]) == 3
    assert "Оценка не выполнена: RuntimeError: сломалось" in capsys.readouterr().err
    conn = connect(tmp_path / "data" / "test.db")
    try:
        assert repo.last_run(conn) is None  # у rescore нет прогона: только вывод
    finally:
        conn.close()


def test_export_writes_excel(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "export"]) == 0
    assert (tmp_path / "exports" / "latest.xlsx").exists()
    assert "Excel:" in capsys.readouterr().out


def test_export_returns_3_when_excel_fails(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)

    def locked(conn, settings, *, now):
        raise ExportError("файл открыт в другой программе")

    monkeypatch.setattr(cli, "export_workbook", locked)
    assert cli.main(["--config", str(cfg), "export"]) == 3
    assert "Excel не сохранён: файл открыт в другой программе" in capsys.readouterr().err


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


TELEGRAM_ENV = "TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=42\n"


def one_listing(monkeypatch):
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))


def test_run_without_telegram_says_so(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    one_listing(monkeypatch)
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert "Telegram не настроен" in capsys.readouterr().out


def test_run_dry_run_prints_planned_messages(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    one_listing(monkeypatch)

    def no_client(settings, token):
        raise AssertionError("--dry-run не должен обращаться к Telegram")

    monkeypatch.setattr(cli, "_telegram_client", no_client)
    assert cli.main(["--config", str(cfg), "run", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--dry-run: уведомлений к отправке 1" in out
    assert "Первый прогон: загружено 1 участок" in out
    assert "<b>" not in out


def test_run_sends_notifications_when_telegram_is_configured(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    one_listing(monkeypatch)
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert "Telegram: отправлено 1" in capsys.readouterr().out
    assert "Первый прогон" in fake.texts()[0]
    assert fake.closed


def test_run_is_refused_while_another_run_holds_the_lock(tmp_path, capsys):
    cfg = write_config(tmp_path)
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "run"]) == 4
    assert "Другой прогон ещё идёт" in capsys.readouterr().err


def test_rescore_is_refused_while_a_run_holds_the_lock(tmp_path):
    cfg = write_config(tmp_path)
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "rescore"]) == 4


def test_broken_settlements_file_is_a_settings_error(tmp_path, capsys):
    cfg = write_config(tmp_path)
    bad = tmp_path / "settlements.yaml"
    bad.write_text("- {id: 1}\n", encoding="utf-8")
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + f"scoring:\n  settlements_path: '{bad.as_posix()}'\n", encoding="utf-8"
    )
    assert cli.main(["--config", str(cfg), "run"]) == 2
    assert "справочник сёл" in capsys.readouterr().err


def test_bad_env_file_is_a_settings_error(tmp_path):
    cfg = write_config(tmp_path, env="TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=abc\n")
    assert cli.main(["--config", str(cfg), "run"]) == 2


def test_digest_dry_run_prints_the_digest(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "digest", "--dry-run"]) == 0
    assert "Дайджест за" in capsys.readouterr().out


def test_digest_needs_telegram(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "digest"]) == 2
    assert "Telegram не настроен" in capsys.readouterr().err


def test_digest_is_sent_once_a_day(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "digest"]) == 0
    assert cli.main(["--config", str(cfg), "digest"]) == 0
    assert "уже отправлялся" in capsys.readouterr().out
    assert len(fake.sent) == 1


def stored_notifications(tmp_path: Path) -> list[tuple[str, str]]:
    conn = connect(tmp_path / "data" / "test.db")
    try:
        return [(row["kind"], row["status"]) for row in conn.execute("SELECT kind, status FROM notifications")]
    finally:
        conn.close()


def test_digest_during_a_run_is_queued_exits_4_and_goes_out_later(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "digest"]) == 4
    captured = capsys.readouterr()
    assert "Идёт другой прогон — дайджест поставлен в очередь и уйдёт со следующей отправкой." in captured.err
    assert "Telegram: отправлено" not in captured.out
    assert fake.sent == [] and fake.closed
    assert stored_notifications(tmp_path) == [("digest", "pending")]
    assert cli.main(["--config", str(cfg), "digest"]) == 0  # прогон закончился: дайджест уходит
    out = capsys.readouterr().out
    assert "ждёт отправки" in out and "Telegram: отправлено 1" in out
    assert len(fake.sent) == 1 and "Дайджест за" in fake.texts()[0]
    assert stored_notifications(tmp_path) == [("digest", "sent")]


def test_digest_already_sent_is_reported_even_while_a_run_holds_the_lock(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "digest"]) == 0
    capsys.readouterr()
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "digest"]) == 0
    captured = capsys.readouterr()
    assert "уже отправлялся" in captured.out and "Идёт другой прогон" not in captured.err
    assert len(fake.sent) == 1


def test_digest_dropped_earlier_is_still_reported_as_failed_while_a_run_holds_the_lock(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram(errors=[TelegramError("Bad Request", permanent=True)])
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "digest"]) == 3
    capsys.readouterr()
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "digest"]) == 3
    captured = capsys.readouterr()
    assert "отправить не удалось" in captured.err and "Идёт другой прогон" not in captured.err
    assert fake.sent == []


def test_digest_dry_run_ignores_the_run_lock_and_queues_nothing(tmp_path, capsys):
    cfg = write_config(tmp_path)
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "digest", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "Дайджест за" in captured.out and "Идёт другой прогон" not in captured.err
    assert stored_notifications(tmp_path) == []


def test_run_returns_3_when_telegram_delivery_fails(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram(errors=[TelegramError("нет сети")])
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    one_listing(monkeypatch)
    assert cli.main(["--config", str(cfg), "run"]) == 3
    assert "не удалось 1" in capsys.readouterr().out


def test_run_returns_3_when_telegram_drops_a_notification(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram(errors=[TelegramError("Bad Request", permanent=True)])
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    one_listing(monkeypatch)
    assert cli.main(["--config", str(cfg), "run"]) == 3
    assert "отброшено 1" in capsys.readouterr().out


def test_digest_rejected_for_good_exits_3_and_is_not_reported_as_sent(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram(errors=[TelegramError("Bad Request", permanent=True)])
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "digest"]) == 3
    assert "отброшено 1" in capsys.readouterr().out
    assert cli.main(["--config", str(cfg), "digest"]) == 3
    captured = capsys.readouterr()
    assert "отправить не удалось" in captured.err
    assert "уже отправлялся" not in captured.out
    assert fake.sent == []


def test_digest_waiting_after_an_outage_is_retried_and_reported(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram(errors=[TelegramError("нет сети")])
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "digest"]) == 3
    assert "не удалось 1" in capsys.readouterr().out
    assert cli.main(["--config", str(cfg), "digest"]) == 0
    out = capsys.readouterr().out
    assert "ждёт отправки" in out and "уже отправлялся" not in out
    assert "Telegram: отправлено 1" in out
    assert len(fake.sent) == 1 and "Дайджест за" in fake.texts()[0]
    assert cli.main(["--config", str(cfg), "digest"]) == 0
    assert "уже отправлялся" in capsys.readouterr().out
    assert len(fake.sent) == 1


def test_telegram_test_without_a_token(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "telegram-test"]) == 2
    assert "@BotFather" in capsys.readouterr().err


def test_telegram_test_lists_chats_when_chat_id_is_missing(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env="TELEGRAM_BOT_TOKEN=t\n")
    update = {"update_id": 1, "message": {"chat": {"id": 555, "first_name": "Тест"}, "text": "привет"}}
    fake = FakeTelegram(updates=[[update]])
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "telegram-test"]) == 2
    out = capsys.readouterr().out
    assert "555 Тест" in out and "TELEGRAM_CHAT_ID" in out


def test_telegram_test_sends_a_message(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "telegram-test"]) == 0
    assert fake.sent == [(42, "✅ Homealize на связи. Команды: /help", None)]
    assert "@homealize_test_bot" in capsys.readouterr().out


def test_serve_needs_telegram(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "serve"]) == 2
    assert "Для serve нужен Telegram" in capsys.readouterr().err


def test_second_serve_is_refused(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: FakeTelegram())
    with hold_lock(tmp_path / "data" / "serve.lock"):
        assert cli.main(["--config", str(cfg), "serve"]) == 4
    assert "serve уже запущен" in capsys.readouterr().err


def test_serve_runs_until_interrupted(tmp_path, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)

    def stop(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.Service, "run_forever", stop)
    assert cli.main(["--config", str(cfg), "serve"]) == 0
    assert fake.closed


def serve_log(tmp_path: Path) -> str:
    reset_logging()  # закрыть файл лога перед чтением
    return (tmp_path / "logs" / "test.log").read_text(encoding="utf-8")


def test_serve_refusals_are_also_logged(tmp_path, monkeypatch):
    """Под pythonw stderr нет: отказ должен остаться хотя бы в логе."""
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "serve"]) == 2
    assert "Для serve нужен Telegram" in serve_log(tmp_path)

    broken = write_config(tmp_path, env="TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=abc\n")
    assert cli.main(["--config", str(broken), "serve"]) == 2
    assert "TELEGRAM_CHAT_ID должен быть целым числом" in serve_log(tmp_path)

    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: FakeTelegram())
    with hold_lock(tmp_path / "data" / "serve.lock"):
        assert cli.main(["--config", str(cfg), "serve"]) == 4
    assert "serve уже запущен" in serve_log(tmp_path)


@pytest.mark.parametrize("command", ["serve", "run", "digest", "telegram-test"])
def test_token_pasted_into_the_chat_id_line_never_reaches_output_or_log(tmp_path, capsys, monkeypatch, command):
    cfg = write_config(tmp_path, env="TELEGRAM_BOT_TOKEN=123456:REALTOKEN\nTELEGRAM_CHAT_ID=123456:SECRET\n")
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: FakeTelegram())
    assert cli.main(["--config", str(cfg), command]) == 2
    captured = capsys.readouterr()
    assert "TELEGRAM_CHAT_ID" in captured.err
    assert "SECRET" not in captured.err + captured.out
    reset_logging()  # закрыть файл лога перед чтением
    log_file = tmp_path / "logs" / "test.log"
    if log_file.exists():
        assert "SECRET" not in log_file.read_text(encoding="utf-8")
    if command == "serve":
        assert "TELEGRAM_CHAT_ID" in log_file.read_text(encoding="utf-8")  # отказ при этом записан в лог


def test_serve_crash_is_logged_and_cleaned_up(tmp_path, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)

    def crash(self):
        raise RuntimeError("неожиданно")

    monkeypatch.setattr(cli.Service, "run_forever", crash)
    assert cli.main(["--config", str(cfg), "serve"]) == 1
    assert fake.closed
    log_text = serve_log(tmp_path)
    assert "serve остановлен из-за ошибки" in log_text and "RuntimeError: неожиданно" in log_text


def test_serve_closes_telegram_when_the_database_does_not_open(tmp_path, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)

    def no_database(path):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(cli, "connect", no_database)
    assert cli.main(["--config", str(cfg), "serve"]) == 1
    assert fake.closed
    assert "unable to open database file" in serve_log(tmp_path)


def test_logging_without_a_console(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)  # так запускает pythonw (автозапуск)
    configure_logging(LoggingSettings(path=tmp_path / "x.log"))
    logging.getLogger("homealize.test").warning("без консоли")
    assert not any(type(handler) is logging.StreamHandler for handler in logging.getLogger().handlers)
    reset_logging()
    assert "без консоли" in (tmp_path / "x.log").read_text(encoding="utf-8")


def dashboard_config(tmp_path: Path, **dashboard) -> Path:
    cfg = write_config(tmp_path)
    lines = "".join(f"  {name}: '{value}'\n" for name, value in dashboard.items())
    cfg.write_text(cfg.read_text(encoding="utf-8") + f"dashboard:\n{lines}", encoding="utf-8")
    return cfg


@pytest.fixture
def streamlit_calls(monkeypatch):
    """Настоящий Streamlit не запускается: запоминаем, с какими аргументами его вызвали бы."""
    calls = []
    monkeypatch.setattr(cli, "_run_streamlit", lambda argv: calls.append(list(argv)) or 0)
    monkeypatch.setenv(CONFIG_ENV, "заглушка")  # после теста переменная вернётся к прежнему значению
    return calls


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_dashboard_starts_streamlit_with_the_settings(tmp_path, capsys, streamlit_calls):
    port = free_port()
    cfg = dashboard_config(tmp_path, host="127.0.0.1", port=port)
    assert cli.main(["--config", str(cfg), "dashboard"]) == 0
    (argv,) = streamlit_calls
    assert argv[:3] == ["streamlit", "run", str(APP_PATH)]
    options = dict(zip(argv[3::2], argv[4::2]))
    assert options == {
        "--server.address": "127.0.0.1",
        "--server.port": str(port),
        "--server.headless": "true",
        "--server.fileWatcherType": "none",
        "--server.runOnSave": "false",
        "--browser.gatherUsageStats": "false",
        "--client.toolbarMode": "viewer",
    }
    assert os.environ[CONFIG_ENV] == str(cfg.resolve())
    out = capsys.readouterr().out
    assert f"http://localhost:{port}" in out
    assert "других устройств" not in out


def test_dashboard_on_all_interfaces_mentions_other_devices(tmp_path, capsys, monkeypatch, streamlit_calls):
    monkeypatch.setattr(cli, "_port_free", lambda host, port: True)
    cfg = dashboard_config(tmp_path, host="0.0.0.0", port=free_port())
    assert cli.main(["--config", str(cfg), "dashboard"]) == 0
    assert "других устройств" in capsys.readouterr().out


def test_dashboard_refuses_a_busy_port(tmp_path, capsys, streamlit_calls):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        cfg = dashboard_config(tmp_path, host="127.0.0.1", port=busy.getsockname()[1])
        assert cli.main(["--config", str(cfg), "dashboard"]) == 4
    assert streamlit_calls == []
    assert "уже запущен" in capsys.readouterr().err


def test_dashboard_writes_output_to_a_file_under_pythonw(tmp_path, monkeypatch, streamlit_calls):
    log_path = tmp_path / "logs" / "dashboard.log"
    cfg = dashboard_config(tmp_path, host="127.0.0.1", port=free_port(), log_path=log_path.as_posix())
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert cli.main(["--config", str(cfg), "dashboard"]) == 0
    stream = sys.stdout
    stream.flush()
    assert sys.stderr is stream
    assert "http://localhost" in log_path.read_text(encoding="utf-8")
    stream.close()
