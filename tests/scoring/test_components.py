import pytest

from homealize.config import ScoringWeights
from homealize.models import Route
from homealize.scoring.components import (
    EXCLUSION_LABELS,
    FLAG_LABELS,
    IN_CITY_ENTRY,
    Component,
    fraction,
    infra_component,
    price_component,
    route_component,
    settlement_component,
    utilities_component,
    weighted_total,
)
from homealize.settlements import SettlementRef

UTILITY_WEIGHTS = {"electricity": 35, "gas": 30, "water": 25, "sewerage": 10}
POI_WEIGHTS = {"supermarket": 0.30, "school": 0.20, "kindergarten": 0.15, "pharmacy": 0.15, "bus_stop": 0.10,
               "clinic": 0.10}


def test_fraction():
    assert fraction(380, 500, 1500) == 1.0
    assert fraction(900, 500, 1500) == pytest.approx(0.6)
    assert fraction(11, 5, 30) == pytest.approx(0.76)
    assert fraction(40, 5, 30) == 0.0


def test_price_component_cheaper_than_market():
    c = price_component(3150.0, 4200.0, label="село Сынжера", n=11, full_discount=0.4, suspicious=False)
    assert c.score == pytest.approx(81.25)
    assert c.note == "Цена 81: на 25% дешевле медианы (4200 €/сот., село Сынжера, объявлений: 11)"


def test_price_component_expensive_is_clamped():
    c = price_component(8400.0, 4200.0, label="весь регион", n=3, full_discount=0.4, suspicious=False)
    assert c.score == 0.0
    assert "на 100% дороже медианы" in c.note


def test_price_component_at_median():
    c = price_component(4200.0, 4200.0, label="весь регион", n=3, full_discount=0.4, suspicious=False)
    assert c.score == 50.0 and "на уровне медианы" in c.note


def test_price_component_suspicious():
    c = price_component(500.0, 4200.0, label="весь регион", n=3, full_discount=0.4, suspicious=True)
    assert c.score == 50.0 and "подозрительно дёшево" in c.note


def test_price_component_without_market():
    c = price_component(4200.0, None, label="", n=0, full_discount=0.4, suspicious=False)
    assert (c.score, c.note) == (50.0, "Цена 50: не с чем сравнить")


def test_route_component():
    c = route_component(Route(km=9.4, minutes=11.0, entry="бул. Дачия (юг)"), best_min=5, worst_min=30)
    assert c.score == pytest.approx(76.0)
    assert c.note == "Дорога 76: 11 мин до въезда «бул. Дачия (юг)» (9.4 км)"


def test_route_component_in_city_and_missing():
    city = route_component(Route(km=0.0, minutes=0.0, entry=IN_CITY_ENTRY), best_min=5, worst_min=30)
    assert (city.score, city.note) == (100.0, "Дорога 100: участок в черте Кишинёва")
    missing = route_component(None, best_min=5, worst_min=30)
    assert missing.score == 0.0 and "не найден" in missing.note


def test_infra_component():
    nearest = {"supermarket": 380, "school": 900, "kindergarten": None, "pharmacy": 450, "bus_stop": 2000,
               "clinic": 1500}
    c = infra_component(nearest, weights=POI_WEIGHTS, full_m=500, zero_m=1500)
    assert c.score == pytest.approx(57.0)
    assert c.note == (
        "Инфраструктура 57: супермаркет 380 м, школа 900 м, детсад нет ближе 1,5 км, аптека 450 м, "
        "остановка нет ближе 1,5 км, медпункт нет ближе 1,5 км"
    )


def test_infra_component_without_poi_data():
    c = infra_component({}, weights=POI_WEIGHTS, full_m=500, zero_m=1500, data_available=False)
    assert (c.score, c.note) == (0.0, "Инфраструктура не оценена: нет данных OpenStreetMap")


def test_infra_component_with_poi_data_by_default():
    c = infra_component({"supermarket": 380}, weights=POI_WEIGHTS, full_m=500, zero_m=1500)
    assert c.note.startswith("Инфраструктура 30: супермаркет 380 м")


def test_utilities_component():
    c, flags = utilities_component({"electricity": 1, "gas": 1, "water": 1, "sewerage": 0}, weights=UTILITY_WEIGHTS)
    assert c.score == 90.0
    assert flags == ["no_sewerage"]
    assert c.note == "Коммуникации 90: есть свет, газ, вода; нет канализации"


def test_utilities_component_nothing_marked():
    c, flags = utilities_component({"electricity": None, "gas": None, "water": None, "sewerage": None},
                                   weights=UTILITY_WEIGHTS)
    assert c.score == 0.0
    assert flags == ["no_electricity", "no_gas", "no_water", "no_sewerage"]
    assert c.note == "Коммуникации 0: ничего не указано; нет электричества, газа, воды, канализации"


def test_settlement_component():
    known, flags = settlement_component(SettlementRef(id=1, name="Сынжера", potential="Высокий", score=85),
                                        unknown_score=50)
    assert (known.score, known.note, flags) == (85, "Село 85: «Высокий» по справочнику", [])
    unknown, flags = settlement_component(None, unknown_score=50)
    assert unknown.score == 50 and flags == ["settlement_unknown"]


def test_weighted_total():
    components = {name: Component(score, "") for name, score in
                  {"price": 80, "route": 76, "infrastructure": 57, "utilities": 90, "settlement": 85}.items()}
    assert weighted_total(components, ScoringWeights()) == 77.1


def test_every_flag_and_exclusion_has_a_label():
    for flag in ["suspicious_price", "coords_approx", "no_coords", "settlement_unknown", "no_electricity",
                 "no_gas", "no_water", "no_sewerage"]:
        assert flag in FLAG_LABELS
    for reason in ["no_coords", "no_route", "too_far", "no_price_eur", "over_budget", "suspicious_price",
                   "duplicate"]:
        assert reason in EXCLUSION_LABELS
