"""Кэш маршрутов OSRM по округлённым координатам. Функции не делают commit."""

import sqlite3
from datetime import datetime

from homealize.models import Route


def get_routes(conn: sqlite3.Connection) -> dict[tuple[float, float], Route | None]:
    """Все посчитанные точки; None — OSRM не нашёл дороги."""
    routes: dict[tuple[float, float], Route | None] = {}
    for row in conn.execute("SELECT lat_round, lon_round, route_km, route_min, entry FROM route_cache"):
        key = (row["lat_round"], row["lon_round"])
        routes[key] = (
            Route(km=row["route_km"], minutes=row["route_min"], entry=row["entry"])
            if row["route_km"] is not None
            else None
        )
    return routes


def put_routes(conn: sqlite3.Connection, routes: dict[tuple[float, float], Route | None], *, now: datetime) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO route_cache (lat_round, lon_round, route_km, route_min, entry, computed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (key[0], key[1], route.km if route else None, route.minutes if route else None,
             route.entry if route else None, now.isoformat())
            for key, route in routes.items()
        ],
    )
