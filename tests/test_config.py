from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from homealize.config import (
    POI_CATEGORIES,
    UTILITIES,
    Settings,
    load_settings,
    load_telegram_secrets,
    parse_hhmm,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_match_spec():
    s = Settings()
    assert (s.filters.area_sot_min, s.filters.area_sot_max) == (5, 10)
    assert s.filters.land_type == "Участок под строительство"
    assert "Кишинёв мун." in s.filters.allowed_regions
    assert s.source.delay_seconds == (2.0, 3.0)
    assert s.source.backoff_seconds == [5.0, 15.0, 45.0]
    assert s.lifecycle.removal_missed_scans == 2


def test_load_settings_overrides_defaults(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "source:\n  page_size: 10\nfilters:\n  allowed_regions: ['Яловены']\n",
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert s.source.page_size == 10
    assert s.filters.allowed_regions == ["Яловены"]
    assert s.filters.area_sot_max == 10


def test_empty_file_gives_defaults(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("", encoding="utf-8")
    assert load_settings(cfg) == Settings()


def test_unknown_key_is_rejected(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("filters:\n  area_max: 12\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(cfg)


def test_inverted_area_range_is_rejected():
    with pytest.raises(ValidationError):
        Settings.model_validate({"filters": {"area_sot_min": 10, "area_sot_max": 5}})


def test_too_few_backoff_pauses_are_rejected():
    with pytest.raises(ValidationError):
        Settings.model_validate({"source": {"max_retries": 3, "backoff_seconds": [5]}})


def test_project_settings_file_is_valid():
    s = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    assert s.filters.area_sot_min == 5
    assert s.storage.db_path == Path("data/homealize.db")


def test_stage2_defaults_match_spec():
    s = Settings()
    w = s.scoring.weights
    assert (w.price, w.route, w.infrastructure, w.utilities, w.settlement) == (0.30, 0.20, 0.20, 0.20, 0.10)
    assert s.scoring.max_price_eur == 50000
    assert s.routing.max_route_km == 30
    assert len(s.routing.entry_points) == 14
    assert s.routing.city_settlement_ids == [13859]
    assert set(s.scoring.poi_weights) == set(POI_CATEGORIES)
    assert set(s.scoring.utility_weights) == set(UTILITIES)
    assert s.export.dir == Path("exports")


def test_project_settings_file_matches_defaults():
    # Единственное отличие от умолчаний — выбор пользователя: дашборд открывается с телефона в той же сети.
    expected = Settings.model_validate({"dashboard": {"host": "0.0.0.0"}})
    assert load_settings(PROJECT_ROOT / "config" / "settings.yaml") == expected


def test_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Settings.model_validate({"scoring": {"weights": {"price": 0.5}}})


def test_osrm_table_size_is_limited():
    with pytest.raises(ValidationError):
        Settings.model_validate({"routing": {"batch_size": 90}})


def test_chain_names_must_be_plain():
    with pytest.raises(ValidationError):
        Settings.model_validate({"poi": {"chains": ["Nr. 1"]}})


def test_unknown_poi_category_is_rejected():
    with pytest.raises(ValidationError):
        Settings.model_validate({"scoring": {"poi_weights": {"bank": 1.0}}})


def test_stage3_defaults_match_spec():
    s = Settings()
    assert s.schedule.run_times == ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"]
    assert (s.schedule.digest_time, s.schedule.timezone, s.schedule.catch_up_gap_minutes) == (
        "20:00", "Europe/Chisinau", 60,
    )
    assert s.schedule.zone.key == "Europe/Chisinau"
    t = s.telegram
    assert (t.new_min_total, t.new_max_rank, t.max_new_per_run) == (70, 10, 10)
    assert (t.price_drop_min_share, t.price_drop_min_total) == (0.05, 60)
    assert (t.alert_repeat_hours, t.digest_top, t.top_default, t.top_max) == (6, 10, 10, 30)
    assert s.storage.run_lock_path == Path("data/run.lock")
    assert s.storage.serve_lock_path == Path("data/serve.lock")


def test_parse_hhmm():
    assert parse_hhmm("07:05") == time(7, 5)


@pytest.mark.parametrize("value", ["7:00", "24:00", "07:60", "seven"])
def test_bad_run_time_is_rejected(value):
    with pytest.raises(ValidationError, match="ЧЧ:ММ"):
        Settings.model_validate({"schedule": {"run_times": [value]}})


def test_bad_digest_time_is_rejected():
    with pytest.raises(ValidationError, match="ЧЧ:ММ"):
        Settings.model_validate({"schedule": {"digest_time": "20-00"}})


def test_run_times_are_sorted_and_unique():
    s = Settings.model_validate({"schedule": {"run_times": ["13:00", "07:00"]}})
    assert s.schedule.run_times == ["07:00", "13:00"]
    with pytest.raises(ValidationError, match="повторяется"):
        Settings.model_validate({"schedule": {"run_times": ["07:00", "07:00"]}})
    with pytest.raises(ValidationError, match="хотя бы одно"):
        Settings.model_validate({"schedule": {"run_times": []}})


@pytest.mark.parametrize("name", ["Mars/Olympus", "Europe"])  # «Europe» — каталог в tzdata: в Windows PermissionError
def test_unknown_timezone_is_rejected(name):
    with pytest.raises(ValidationError, match="часовой пояс"):
        Settings.model_validate({"schedule": {"timezone": name}})


def test_top_default_cannot_exceed_top_max():
    with pytest.raises(ValidationError):
        Settings.model_validate({"telegram": {"top_default": 40, "top_max": 30}})


def test_env_file_is_read(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# комментарий\nTELEGRAM_BOT_TOKEN='123:abc'\nexport TELEGRAM_CHAT_ID=42\n\n", encoding="utf-8"
    )
    secrets = load_telegram_secrets(env, environ={})
    assert (secrets.token, secrets.chat_id) == ("123:abc", 42)
    assert "123:abc" not in repr(secrets)


def test_env_file_with_bom(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=t\n", encoding="utf-8-sig")
    assert load_telegram_secrets(env, environ={}).token == "t"


def test_environment_overrides_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=file\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    secrets = load_telegram_secrets(env, environ={"TELEGRAM_BOT_TOKEN": "env", "TELEGRAM_CHAT_ID": "2"})
    assert (secrets.token, secrets.chat_id) == ("env", 2)


def test_missing_token_means_telegram_is_off(tmp_path):
    assert load_telegram_secrets(tmp_path / "нет.env", environ={}) is None
    empty = tmp_path / ".env"
    empty.write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    assert load_telegram_secrets(empty, environ={}) is None


def test_token_without_chat_id(tmp_path):
    secrets = load_telegram_secrets(tmp_path / "нет.env", environ={"TELEGRAM_BOT_TOKEN": "t"})
    assert (secrets.token, secrets.chat_id) == ("t", None)


def test_bad_chat_id_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="целым числом"):
        load_telegram_secrets(tmp_path / "нет.env", environ={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "abc"})


def test_bad_chat_id_error_never_contains_the_value(tmp_path):
    """Токен, вставленный по ошибке в строку TELEGRAM_CHAT_ID, не должен попасть в сообщение (а значит, в лог)."""
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=123456:REALTOKEN\nTELEGRAM_CHAT_ID=123456:SECRET\n", encoding="utf-8")
    for environ in ({}, {"TELEGRAM_CHAT_ID": "123456:SECRET"}):
        with pytest.raises(ValueError, match="TELEGRAM_CHAT_ID") as caught:
            load_telegram_secrets(env, environ=environ)
        message = str(caught.value)
        assert "SECRET" not in message and "123456:" not in message
        assert caught.value.__suppress_context__  # `from None`: исходная ошибка int() со значением не печатается


def test_bad_env_line_is_an_error_without_its_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN 123:secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="строка 1") as caught:
        load_telegram_secrets(env, environ={})
    assert "secret" not in str(caught.value)


def test_stage4_defaults_match_spec():
    d = Settings().dashboard
    assert (d.host, d.port, d.log_path) == ("127.0.0.1", 8501, Path("logs/dashboard.log"))
    assert (d.photos, d.market_min_n, d.market_weeks, d.market_compare_weeks) == (6, 3, 12, 4)


def test_dashboard_port_must_be_unprivileged():
    with pytest.raises(ValidationError):
        Settings.model_validate({"dashboard": {"port": 80}})
