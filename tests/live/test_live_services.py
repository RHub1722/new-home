"""Проверка внешних сервисов оценки (по одному небольшому запросу). Запускать вручную: uv run pytest -m live."""

from datetime import date

import pytest

from homealize.config import CurrencySettings, PoiSettings, RoutingSettings
from homealize.enrich.currency import fetch_bnm_rates
from homealize.enrich.poi import fetch_pois
from homealize.enrich.routing import OsrmRouter
from homealize.evaluate import build_http_client

pytestmark = pytest.mark.live


def test_osrm_routes_from_singera():
    with build_http_client() as http:
        route = OsrmRouter(http, RoutingSettings()).table([(46.931, 28.939)])[(46.931, 28.939)]
    assert route is not None
    assert 0 < route.minutes < 30


def test_bnm_rates():
    with build_http_client() as http:
        rates = fetch_bnm_rates(http, CurrencySettings(), date(2026, 10, 29))
    assert 15 < rates["EUR"] < 30


def test_overpass_small_area():
    with build_http_client() as http:
        pois = fetch_pois(http, PoiSettings(radius_km=1.0))
    assert len(pois) >= 10
