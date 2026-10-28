from pathlib import Path

import pytest
from pydantic import ValidationError

from homealize.config import Settings, load_settings

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
