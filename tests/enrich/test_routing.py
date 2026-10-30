from datetime import UTC, datetime

import httpx
import pytest

from homealize.config import EntryPoint, RoutingSettings
from homealize.enrich.routing import OsrmRouter, RoutingError, ensure_routes, round_key
from homealize.models import Route
from homealize.storage import route_cache

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
SETTINGS = RoutingSettings(
    entry_points=[EntryPoint(name="A", lat=47.0, lon=28.8), EntryPoint(name="B", lat=46.9, lon=28.9)],
    batch_size=2,
    pause_seconds=1.0,
)


def osrm(requests, rows):
    """rows — строки durations (секунды) по одной на каждый источник, в порядке запросов."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        n = len(request.url.params["sources"].split(";"))
        durations = [rows.pop(0) for _ in range(n)]
        distances = [[None if d is None else d * 10 for d in row] for row in durations]
        return httpx.Response(200, json={"code": "Ok", "durations": durations, "distances": distances})

    return handler


def router_with(handler) -> OsrmRouter:
    return OsrmRouter(httpx.Client(transport=httpx.MockTransport(handler)), SETTINGS)


def test_round_key():
    assert round_key(47.09921, 28.81654) == (47.099, 28.817)


def test_table_request_and_best_entry():
    requests = []
    router = router_with(osrm(requests, [[600, 300], [None, 900]]))
    routes = router.table([(46.93, 28.94), (47.25, 28.73)])
    assert routes[(46.93, 28.94)] == Route(km=3.0, minutes=5.0, entry="B")
    assert routes[(47.25, 28.73)] == Route(km=9.0, minutes=15.0, entry="B")
    url = str(requests[0].url)
    assert "/table/v1/driving/28.940000,46.930000;28.730000,47.250000;28.800000,47.000000;28.900000,46.900000" in url
    assert requests[0].url.params["sources"] == "0;1"
    assert requests[0].url.params["destinations"] == "2;3"


def test_unreachable_point_gives_none():
    router = router_with(osrm([], [[None, None]]))
    assert router.table([(46.93, 28.94)]) == {(46.93, 28.94): None}


def test_error_code_raises():
    router = router_with(lambda request: httpx.Response(200, json={"code": "InvalidQuery", "message": "bad"}))
    with pytest.raises(RoutingError, match="InvalidQuery"):
        router.table([(46.93, 28.94)])


def test_ensure_routes_batches_caches_and_pauses(db):
    requests, sleeps = [], []
    router = router_with(osrm(requests, [[60, 120], [120, 60], [600, 600]]))
    points = {(46.1, 28.1), (46.2, 28.2), (46.3, 28.3)}
    routes, warning = ensure_routes(db, router, points, now=T0, sleep=sleeps.append)
    assert warning is None
    assert len(requests) == 2 and sleeps == [1.0]
    assert routes[(46.1, 28.1)].entry == "A"
    assert routes[(46.2, 28.2)].entry == "B"
    again, _ = ensure_routes(db, router, points, now=T0, sleep=sleeps.append)
    assert again == routes and len(requests) == 2
    assert len(route_cache.get_routes(db)) == 3


def test_failure_keeps_finished_batches(db):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(502)
        return httpx.Response(200, json={"code": "Ok", "durations": [[60, 120], [60, 120]],
                                          "distances": [[600, 1200], [600, 1200]]})

    router = router_with(handler)
    points = {(46.1, 28.1), (46.2, 28.2), (46.3, 28.3)}
    routes, warning = ensure_routes(db, router, points, now=T0, sleep=lambda seconds: None)
    assert len(routes) == 2
    assert "Маршруты не посчитаны для 1 точек" in warning
    assert len(route_cache.get_routes(db)) == 2


def test_null_matrix_raises_routing_error():
    router = router_with(lambda request: httpx.Response(200, json={"code": "Ok", "durations": None, "distances": None}))
    with pytest.raises(RoutingError):
        router.table([(46.93, 28.94)])


def test_short_matrix_raises_routing_error():
    router = router_with(lambda request: httpx.Response(
        200, json={"code": "Ok", "durations": [[60, 120]], "distances": [[600, 1200]]}))
    with pytest.raises(RoutingError):
        router.table([(46.93, 28.94), (47.25, 28.73)])


def test_ragged_row_raises_routing_error():
    router = router_with(lambda request: httpx.Response(
        200, json={"code": "Ok", "durations": [[60]], "distances": [[600]]}))
    with pytest.raises(RoutingError):
        router.table([(46.93, 28.94)])


def test_non_dict_body_raises_routing_error():
    router = router_with(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(RoutingError):
        router.table([(46.93, 28.94)])


def test_malformed_answer_becomes_warning(db):
    router = router_with(lambda request: httpx.Response(200, json={"code": "Ok", "durations": None, "distances": None}))
    routes, warning = ensure_routes(db, router, {(46.1, 28.1)}, now=T0, sleep=lambda seconds: None)
    assert routes == {}
    assert "Маршруты не посчитаны" in warning
    assert route_cache.get_routes(db) == {}
