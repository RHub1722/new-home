from datetime import UTC, datetime, timedelta

import httpx
import pytest

from homealize.config import DEFAULT_CHAINS, PoiSettings
from homealize.enrich.poi import PoiIndex, build_query, classify, ensure_pois, parse_elements
from homealize.models import Poi
from homealize.storage import poi_cache

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
CHAINS = ["Linella", "Kaufland"]
PAYLOAD = {
    "elements": [
        {"type": "node", "id": 1, "lat": 47.0, "lon": 28.8, "tags": {"shop": "supermarket", "name": "Linella"}},
        {"type": "way", "id": 2, "center": {"lat": 47.01, "lon": 28.81}, "tags": {"amenity": "school", "name": "Liceul 1"}},
        {"type": "node", "id": 3, "lat": 47.02, "lon": 28.82, "tags": {"highway": "bus_stop"}},
        {"type": "node", "id": 4, "lat": 47.03, "lon": 28.83, "tags": {"amenity": "bank"}},
        {"type": "relation", "id": 5, "tags": {"amenity": "hospital"}},
        {"type": "node", "id": 1, "lat": 47.0, "lon": 28.8, "tags": {"shop": "supermarket", "name": "Linella"}},
    ]
}


def make_client(responses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[min(len(calls), len(responses)) - 1]

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_build_query_covers_categories_and_area():
    query = build_query(PoiSettings(chains=CHAINS))
    assert "around:35000,47.0245,28.8327" in query
    assert '"shop"="supermarket"' in query
    assert "Linella|Kaufland" in query
    assert "school|kindergarten|pharmacy|clinic|doctors|hospital" in query
    assert query.rstrip().endswith("out center tags;")


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"shop": "supermarket"}, "supermarket"),
        ({"shop": "convenience", "brand": "Linella"}, "supermarket"),
        ({"shop": "convenience", "name": "Mini Kaufland"}, "supermarket"),
        ({"shop": "convenience", "name": "Magazin"}, None),
        ({"amenity": "school"}, "school"),
        ({"amenity": "kindergarten"}, "kindergarten"),
        ({"amenity": "pharmacy"}, "pharmacy"),
        ({"amenity": "doctors"}, "clinic"),
        ({"amenity": "hospital"}, "clinic"),
        ({"highway": "bus_stop"}, "bus_stop"),
        ({"public_transport": "platform"}, "bus_stop"),
        ({"amenity": "bank"}, None),
    ],
)
def test_classify(tags, expected):
    assert classify(tags, CHAINS) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"shop": "bakery", "name": "Local Bakery"}, None),
        ({"shop": "convenience", "name": "Magazin Localitate"}, None),
        ({"shop": "hairdresser", "name": "Salon Nr 10"}, None),
        ({"shop": "convenience", "name": "Nr 10"}, None),
        ({"shop": "convenience", "name": "Nr 1"}, "supermarket"),
        ({"shop": "convenience", "brand": "Local Discounter"}, "supermarket"),
        ({"shop": "convenience", "name": "LINELLA"}, "supermarket"),
    ],
)
def test_classify_with_default_chains(tags, expected):
    assert classify(tags, DEFAULT_CHAINS) == expected


def test_parse_elements_takes_coordinates_and_skips_junk():
    pois = {poi.key: poi for poi in parse_elements(PAYLOAD, CHAINS)}
    assert set(pois) == {"node/1", "way/2", "node/3"}
    assert pois["way/2"] == Poi(key="way/2", category="school", name="Liceul 1", brand=None, lat=47.01, lon=28.81)
    assert pois["node/1"].category == "supermarket"


def test_poi_index_finds_nearest_per_category():
    index = PoiIndex([
        Poi("node/1", "supermarket", None, None, 47.0036, 28.8),
        Poi("node/2", "supermarket", None, None, 47.0100, 28.8),
        Poi("node/3", "school", None, None, 47.0099, 28.8),
        Poi("node/4", "pharmacy", None, None, 47.0900, 28.8),
    ])
    nearest = index.nearest_m(47.0, 28.8)
    assert nearest["supermarket"] == pytest.approx(400, abs=2)
    assert nearest["school"] == pytest.approx(1101, abs=2)
    assert nearest["pharmacy"] is None
    assert nearest["bus_stop"] is None


def test_poi_index_knows_whether_it_has_objects():
    assert not PoiIndex([]).has_objects
    assert PoiIndex([Poi("node/1", "school", None, None, 47.0, 28.8)]).has_objects


def test_ensure_pois_downloads_once_and_caches(db):
    client, calls = make_client([httpx.Response(200, json=PAYLOAD)])
    settings = PoiSettings(chains=CHAINS)
    pois, warning = ensure_pois(db, client, settings, now=T0)
    assert warning is None and len(pois) == 3
    assert calls[0].method == "POST"
    pois, warning = ensure_pois(db, client, settings, now=T0 + timedelta(days=10))
    assert len(calls) == 1 and len(pois) == 3
    assert poi_cache.poi_fetched_at(db) == T0


def test_stale_pois_are_refreshed(db):
    client, calls = make_client([httpx.Response(200, json=PAYLOAD)])
    settings = PoiSettings(chains=CHAINS)
    ensure_pois(db, client, settings, now=T0)
    ensure_pois(db, client, settings, now=T0 + timedelta(days=31))
    assert len(calls) == 2


def test_failed_refresh_keeps_old_pois(db):
    client, _ = make_client([httpx.Response(200, json=PAYLOAD), httpx.Response(504)])
    settings = PoiSettings(chains=CHAINS)
    ensure_pois(db, client, settings, now=T0)
    pois, warning = ensure_pois(db, client, settings, now=T0, force=True)
    assert len(pois) == 3
    assert "POI не обновлены" in warning


def test_empty_answer_is_not_saved(db):
    client, _ = make_client([httpx.Response(200, json={"elements": []})])
    pois, warning = ensure_pois(db, client, PoiSettings(chains=CHAINS), now=T0)
    assert pois == []
    assert "0 объектов" in warning


def test_partial_overpass_answer_keeps_old_pois(db):
    partial = {
        "elements": [PAYLOAD["elements"][0]],
        "remark": 'runtime error: Query timed out in "query" at line 3 after 240 seconds.',
    }
    client, _ = make_client([httpx.Response(200, json=PAYLOAD), httpx.Response(200, json=partial)])
    settings = PoiSettings(chains=CHAINS)
    ensure_pois(db, client, settings, now=T0)
    pois, warning = ensure_pois(db, client, settings, now=T0 + timedelta(days=1), force=True)
    assert len(pois) == 3
    assert "POI не обновлены" in warning
    assert poi_cache.poi_fetched_at(db) == T0


def test_failure_with_empty_cache_says_infrastructure_is_not_scored(db):
    client, _ = make_client([httpx.Response(502)])
    pois, warning = ensure_pois(db, client, PoiSettings(chains=CHAINS), now=T0)
    assert pois == []
    assert "POI не обновлены (HTTP 502)" in warning
    assert "http" not in warning
    assert "сохранённых нет — инфраструктура не оценена" in warning
    assert "`uv run homealize refresh-poi`, затем `uv run homealize rescore`" in warning
    assert "используются сохранённые" not in warning


def test_failure_with_saved_pois_says_they_are_used(db):
    client, _ = make_client([httpx.Response(200, json=PAYLOAD), httpx.Response(502)])
    settings = PoiSettings(chains=CHAINS)
    ensure_pois(db, client, settings, now=T0)
    pois, warning = ensure_pois(db, client, settings, now=T0, force=True)
    assert len(pois) == 3
    assert warning == "POI не обновлены (HTTP 502), используются сохранённые"


@pytest.mark.parametrize(
    "payload",
    [
        {"elements": "x"},  # AttributeError: у строки нет .get
        {"elements": None},  # TypeError: None не перебирается
        {"elements": [{"type": "node", "lat": 47.0, "lon": 28.8, "tags": {"amenity": "school"}}]},  # KeyError: нет id
        [],  # AttributeError: ответ не объект
    ],
)
def test_malformed_overpass_payload_falls_back_and_keeps_cache(db, payload):
    client, _ = make_client([httpx.Response(200, json=PAYLOAD), httpx.Response(200, json=payload)])
    settings = PoiSettings(chains=CHAINS)
    ensure_pois(db, client, settings, now=T0)
    pois, warning = ensure_pois(db, client, settings, now=T0 + timedelta(days=1), force=True)
    assert len(pois) == 3
    assert "POI не обновлены" in warning and "используются сохранённые" in warning
    assert poi_cache.poi_fetched_at(db) == T0


def test_malformed_overpass_payload_with_empty_cache_is_a_warning(db):
    client, _ = make_client([httpx.Response(200, json={"elements": "x"})])
    pois, warning = ensure_pois(db, client, PoiSettings(chains=CHAINS), now=T0)
    assert pois == []
    assert "сохранённых нет" in warning
