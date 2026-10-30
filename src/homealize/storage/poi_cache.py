"""Объекты инфраструктуры (POI) в базе. Функции не делают commit."""

import sqlite3
from datetime import datetime

from homealize.models import Poi


def replace_poi(conn: sqlite3.Connection, pois: list[Poi], *, now: datetime) -> None:
    conn.execute("DELETE FROM poi")
    conn.executemany(
        "INSERT INTO poi (osm_key, category, name, brand, lat, lon, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(p.key, p.category, p.name, p.brand, p.lat, p.lon, now.isoformat()) for p in pois],
    )


def load_poi(conn: sqlite3.Connection) -> list[Poi]:
    return [
        Poi(key=r["osm_key"], category=r["category"], name=r["name"], brand=r["brand"], lat=r["lat"], lon=r["lon"])
        for r in conn.execute("SELECT * FROM poi ORDER BY osm_key")
    ]


def poi_fetched_at(conn: sqlite3.Connection) -> datetime | None:
    value = conn.execute("SELECT MAX(fetched_at) FROM poi").fetchone()[0]
    return datetime.fromisoformat(value) if value else None
