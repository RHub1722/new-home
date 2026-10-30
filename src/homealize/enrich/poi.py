"""Инфраструктура из OpenStreetMap: запрос Overpass, классификация, поиск ближайших объектов."""

import math
import re
import sqlite3
from datetime import datetime, timedelta

import httpx

from homealize.config import POI_CATEGORIES, PoiSettings
from homealize.enrich.errors import describe_error
from homealize.geo import haversine_m
from homealize.models import Poi
from homealize.storage import poi_cache

SEARCH_RADIUS_M = 5000.0  # дальше ближайший объект не ищем: для оценки важно до 1,5 км
_METERS_PER_DEGREE_LAT = 111_320.0
# Магазины, где сеть из settings.chains считается продуктовой; остальные «шопы» с похожим названием — нет.
GROCERY_SHOPS = frozenset({"convenience", "grocery", "general", "department_store"})


def build_query(settings: PoiSettings) -> str:
    around = f"around:{round(settings.radius_km * 1000)},{settings.center_lat},{settings.center_lon}"
    chains = "|".join(settings.chains)
    return "\n".join([
        f"[out:json][timeout:{round(settings.timeout_seconds)}];",
        "(",
        f'  nwr["shop"="supermarket"]({around});',
        f'  nwr["shop"]["brand"~"{chains}",i]({around});',
        f'  nwr["shop"]["name"~"{chains}",i]({around});',
        f'  nwr["amenity"~"^(school|kindergarten|pharmacy|clinic|doctors|hospital)$"]({around});',
        f'  node["highway"="bus_stop"]({around});',
        f'  node["public_transport"="platform"]({around});',
        ");",
        "out center tags;",
    ])


def classify(tags: dict[str, str], chains: list[str]) -> str | None:
    """Категория объекта для оценки или None, если он не нужен."""
    shop = tags.get("shop")
    if shop == "supermarket" or (shop in GROCERY_SHOPS and _is_chain(tags, chains)):
        return "supermarket"
    amenity = tags.get("amenity")
    if amenity in ("school", "kindergarten", "pharmacy"):
        return amenity
    if amenity in ("clinic", "doctors", "hospital"):
        return "clinic"
    if tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform":
        return "bus_stop"
    return None


def parse_elements(payload: dict, chains: list[str]) -> list[Poi]:
    pois: dict[str, Poi] = {}
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        category = classify(tags, chains)
        centre = element.get("center") or {}
        lat = element.get("lat", centre.get("lat"))
        lon = element.get("lon", centre.get("lon"))
        if category is None or lat is None or lon is None:
            continue
        key = f"{element['type']}/{element['id']}"
        pois[key] = Poi(key=key, category=category, name=tags.get("name"), brand=tags.get("brand"),
                        lat=float(lat), lon=float(lon))
    return list(pois.values())


def fetch_pois(client: httpx.Client, settings: PoiSettings) -> list[Poi]:
    response = client.post(settings.overpass_url, data={"data": build_query(settings)},
                           timeout=settings.timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    remark = payload.get("remark") or ""
    if "runtime error" in remark.casefold():
        raise ValueError(f"Overpass ответил с ошибкой: {remark}")
    return parse_elements(payload, settings.chains)


def ensure_pois(
    conn: sqlite3.Connection, client: httpx.Client, settings: PoiSettings, *, now: datetime, force: bool = False
) -> tuple[list[Poi], str | None]:
    """POI из базы; обновляет их из Overpass, если данных нет, они старше refresh_days или force."""
    fetched = poi_cache.poi_fetched_at(conn)
    stale = force or fetched is None or now - fetched > timedelta(days=settings.refresh_days)
    failure = None
    if stale:
        try:
            pois = fetch_pois(client, settings)
            if not pois:
                raise ValueError("Overpass вернул 0 объектов")
            poi_cache.replace_poi(conn, pois, now=now)
            conn.commit()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # KeyError, TypeError, AttributeError: ответ другой формы, чем ждёт parse_elements
            failure = describe_error(exc)
    cached = poi_cache.load_poi(conn)
    warning = None
    if failure is not None:
        if cached:
            warning = f"POI не обновлены ({failure}), используются сохранённые"
        else:
            warning = (
                f"POI не обновлены ({failure}), сохранённых нет — инфраструктура не оценена; "
                "выполните `uv run homealize refresh-poi`, затем `uv run homealize rescore`"
            )
    return cached, warning


class PoiIndex:
    """Поиск ближайшего объекта каждой категории."""

    def __init__(self, pois: list[Poi]) -> None:
        self._by_category: dict[str, list[Poi]] = {category: [] for category in POI_CATEGORIES}
        for poi in pois:
            self._by_category.setdefault(poi.category, []).append(poi)

    @property
    def has_objects(self) -> bool:
        return any(self._by_category.values())

    def nearest_m(self, lat: float, lon: float) -> dict[str, int | None]:
        dlat = SEARCH_RADIUS_M / _METERS_PER_DEGREE_LAT
        dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
        result: dict[str, int | None] = {}
        for category in POI_CATEGORIES:
            best = None
            for poi in self._by_category.get(category, ()):
                if abs(poi.lat - lat) > dlat or abs(poi.lon - lon) > dlon:
                    continue
                distance = haversine_m(lat, lon, poi.lat, poi.lon)
                if distance <= SEARCH_RADIUS_M and (best is None or distance < best):
                    best = distance
            result[category] = round(best) if best is not None else None
        return result


def _is_chain(tags: dict[str, str], chains: list[str]) -> bool:
    """Название сети есть в brand или name как отдельное слово (без буквы или цифры рядом)."""
    text = f"{tags.get('brand', '')} {tags.get('name', '')}".casefold()
    return any(re.search(rf"(?<![\w]){re.escape(chain.casefold())}(?![\w])", text) for chain in chains)
