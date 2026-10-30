import json
from datetime import UTC, datetime, timedelta

import pytest

from homealize.config import Settings
from homealize.enrich.poi import PoiIndex
from homealize.enrich.routing import round_key
from homealize.models import Poi, Route
from homealize.scoring.components import IN_CITY_ENTRY
from homealize.scoring.engine import Services, rescore, route_points
from homealize.settlements import SettlementRef
from homealize.storage import repo
from tests.factories import make_listing

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
SETTINGS = Settings()
SINGERA, CITY = 14398, 13859
NEAR = Route(km=8.0, minutes=10.0, entry="M3")


class AnyRoute(dict):
    """Маршрут по умолчанию для любой точки; явно заданные ключи (в том числе None) важнее."""

    def __init__(self, default):
        super().__init__()
        self.default = default

    def get(self, key, default=None):
        return super().get(key, self.default)


def add(db, lid, pps, *, area=6.0, lat=46.930, lon=28.940, settlement_id=SINGERA, settlement="Сынжера",
        status="active", **extra):
    listing = make_listing(id=lid, url=f"https://999.md/ru/{lid}", price_original=pps * area, area_sot=area,
                           lat=lat, lon=lon, settlement_id=settlement_id, settlement=settlement, **extra)
    repo.upsert_listing(db, listing, status=status, filter_reason=None, now=T0)
    db.commit()


def services(routes=None, pois=()):
    return Services(
        routes=routes if routes is not None else AnyRoute(NEAR),
        poi_index=PoiIndex(list(pois)),
        rates=None,
        settlements={SINGERA: SettlementRef(id=SINGERA, name="Сынжера", potential="Высокий", score=85)},
    )


def scores(db):
    return {row["listing_id"]: row for row in db.execute("SELECT * FROM scores")}


def six_normal(db):
    for i, pps in enumerate([4000, 4500, 5000, 5500, 6000, 6500]):
        add(db, f"n{i}", pps, lat=46.930 + i * 0.002)


def test_ranking_by_price_when_everything_else_is_equal(db):
    six_normal(db)
    summary = rescore(db, SETTINGS, services(), now=T0)
    s = scores(db)
    assert (summary.scored, summary.eligible) == (6, 6)
    assert [s[f"n{i}"]["rank"] for i in range(6)] == [1, 2, 3, 4, 5, 6]
    first = s["n0"]
    assert (first["median_level"], first["median_n"], first["market_median_eur_per_sot"]) == ("settlement", 6, 5250)
    components = json.loads(first["components"])
    assert components["price"] == pytest.approx(50 + 50 * (1250 / 5250) / 0.4)
    assert components["route"] == pytest.approx(80.0)
    assert components["settlement"] == 85
    assert components["utilities"] == 90
    assert (first["nearest_entry"], first["route_km"]) == ("M3", 8.0)
    assert "село Сынжера" in json.loads(first["explanations"])["price"]
    assert summary.best_total == first["total"]


def test_exclusion_reasons(db):
    six_normal(db)
    add(db, "over", 9000)
    add(db, "far", 5000, lat=47.300)
    add(db, "noroute", 5000, lat=47.400)
    add(db, "nocoords", 5000, lat=None, lon=None, settlement_id=77777, settlement="Нигде")
    add(db, "cheap", 500, lat=46.960)
    add(db, "dup", 4100, lat=46.930)
    add(db, "city", 6000, lat=47.020, lon=28.830, settlement_id=CITY, settlement="Кишинёв")
    routes = AnyRoute(NEAR)
    routes[round_key(47.300, 28.940)] = Route(km=40.0, minutes=45.0, entry="M3")
    routes[round_key(47.400, 28.940)] = None
    rescore(db, SETTINGS, services(routes), now=T0)
    s = scores(db)
    reasons = {lid: row["excluded_reason"] for lid, row in s.items()}
    assert reasons["over"] == "over_budget"
    assert reasons["far"] == "too_far"
    assert reasons["noroute"] == "no_route"
    assert reasons["nocoords"] == "no_coords"
    assert reasons["cheap"] == "suspicious_price"
    assert reasons["dup"] == "duplicate"
    assert reasons["n0"] is None and reasons["city"] is None
    assert (s["city"]["route_km"], s["city"]["nearest_entry"]) == (0, IN_CITY_ENTRY)
    assert "suspicious_price" in json.loads(s["cheap"]["flags"])
    assert (s["dup"]["duplicate_group"], s["dup"]["is_group_primary"]) == ("dup", 0)
    assert (s["n0"]["duplicate_group"], s["n0"]["is_group_primary"]) == ("dup", 1)
    assert s["over"]["rank"] is None


def test_listing_without_coordinates_uses_settlement_centre(db):
    six_normal(db)
    add(db, "approx", 5000, lat=None, lon=None)
    rescore(db, SETTINGS, services(), now=T0)
    row = scores(db)["approx"]
    assert (row["coords_approx"], row["excluded_reason"]) == (1, None)
    assert row["lat"] == pytest.approx(46.935) and row["lon"] == pytest.approx(28.940)
    assert "coords_approx" in json.loads(row["flags"])


def test_recently_removed_listing_feeds_the_median(db):
    for i, pps in enumerate([4000, 5000, 6000, 7000]):
        add(db, f"a{i}", pps, lat=46.930 + i * 0.002)
    add(db, "gone", 8000, lat=46.950, status="removed")
    rescore(db, SETTINGS, services(), now=T0 + timedelta(days=1))
    s = scores(db)
    assert set(s) == {"a0", "a1", "a2", "a3"}
    assert (s["a0"]["median_level"], s["a0"]["median_n"], s["a0"]["market_median_eur_per_sot"]) == (
        "settlement", 5, 6000)


def test_old_removed_listing_is_ignored(db):
    for i, pps in enumerate([4000, 5000, 6000, 7000]):
        add(db, f"a{i}", pps, lat=46.930 + i * 0.002)
    add(db, "gone", 8000, lat=46.950, status="removed")
    rescore(db, SETTINGS, services(), now=T0 + timedelta(days=100))
    row = scores(db)["a0"]
    assert (row["median_level"], row["median_n"]) == ("region", 4)


def test_infrastructure_uses_poi_index(db):
    add(db, "solo", 5000)
    rescore(db, SETTINGS, services(pois=[Poi("node/1", "supermarket", "Linella", None, 46.9336, 28.940)]), now=T0)
    row = scores(db)["solo"]
    assert json.loads(row["poi_nearest_m"])["supermarket"] == pytest.approx(400, abs=2)
    assert json.loads(row["components"])["infrastructure"] == pytest.approx(30.0)


def test_rescore_replaces_previous_snapshot(db):
    six_normal(db)
    rescore(db, SETTINGS, services(), now=T0)
    rescore(db, SETTINGS, services(), now=T0)
    assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 6


def test_route_points(db):
    add(db, "p1", 5000, lat=46.93012, lon=28.94049)
    add(db, "p2", 5000, lat=None, lon=None)
    add(db, "city", 6000, lat=47.02, lon=28.83, settlement_id=CITY, settlement="Кишинёв")
    repo.upsert_listing(db, make_listing(id="f", lat=46.5, lon=28.5, settlement_id=SINGERA),
                        status="filtered_out", filter_reason="region", now=T0)
    db.commit()
    assert route_points(db, SETTINGS, now=T0) == {round_key(46.93012, 28.94049)}
