from pathlib import Path

import pytest
from pydantic import ValidationError

from homealize.config import POI_CATEGORIES, UTILITIES, Settings, load_settings

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
    assert load_settings(PROJECT_ROOT / "config" / "settings.yaml") == Settings()


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
