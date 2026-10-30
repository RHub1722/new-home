"""Маршруты OSRM до ближайшей точки въезда в Кишинёв, с кэшем в базе."""

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from homealize.config import RoutingSettings
from homealize.models import Route
from homealize.storage import route_cache


class RoutingError(Exception):
    """OSRM ответил кодом ошибки."""


def round_key(lat: float, lon: float) -> tuple[float, float]:
    """Ключ кэша: координаты с точностью ~100 м."""
    return (round(lat, 3), round(lon, 3))


def _matrix_ok(value: object, rows: int, cols: int) -> bool:
    """Таблица OSRM: список из rows строк, в каждой по cols значений."""
    return (
        isinstance(value, list)
        and len(value) == rows
        and all(isinstance(row, list) and len(row) == cols for row in value)
    )


class OsrmRouter:
    def __init__(self, client: httpx.Client, settings: RoutingSettings) -> None:
        self._client = client
        self._settings = settings

    @property
    def batch_size(self) -> int:
        return self._settings.batch_size

    @property
    def pause_seconds(self) -> float:
        return self._settings.pause_seconds

    def table(self, points: list[tuple[float, float]]) -> dict[tuple[float, float], Route | None]:
        """Один табличный запрос: от каждой точки (lat, lon) до всех точек въезда; берётся самая быстрая."""
        entries = self._settings.entry_points
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
        coords += ";" + ";".join(f"{entry.lon:.6f},{entry.lat:.6f}" for entry in entries)
        n = len(points)
        sources = ";".join(str(i) for i in range(n))
        destinations = ";".join(str(n + j) for j in range(len(entries)))
        url = (
            f"{self._settings.osrm_url}/table/v1/driving/{coords}"
            f"?sources={sources}&destinations={destinations}&annotations=duration,distance"
        )
        response = self._client.get(url, timeout=self._settings.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RoutingError("OSRM вернул неожиданный ответ")
        if data.get("code") != "Ok":
            raise RoutingError(f"OSRM: {data.get('code')} {data.get('message', '')}".strip())
        cols = len(entries)
        if not (_matrix_ok(data.get("durations"), n, cols) and _matrix_ok(data.get("distances"), n, cols)):
            raise RoutingError(f"OSRM вернул неполный ответ: ожидалось {n} строк")
        routes: dict[tuple[float, float], Route | None] = {}
        for point, durations, distances in zip(points, data["durations"], data["distances"]):
            best = None
            for entry, duration, distance in zip(entries, durations, distances):
                if duration is None or distance is None:
                    continue
                if best is None or duration < best[0]:
                    best = (duration, distance, entry.name)
            routes[point] = (
                Route(km=round(best[1] / 1000, 2), minutes=round(best[0] / 60, 1), entry=best[2]) if best else None
            )
        return routes


def ensure_routes(
    conn: sqlite3.Connection,
    router: OsrmRouter,
    points: set[tuple[float, float]],
    *,
    now: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[tuple[float, float], Route | None], str | None]:
    """Маршруты для точек (уже округлённых round_key): из кэша, недостающие — пачками из OSRM."""
    cached = route_cache.get_routes(conn)
    missing = sorted(point for point in points if point not in cached)
    warning = None
    for start in range(0, len(missing), router.batch_size):
        if start:
            sleep(router.pause_seconds)
        batch = missing[start:start + router.batch_size]
        try:
            fresh = router.table(batch)
        except (httpx.HTTPError, RoutingError, ValueError, KeyError) as exc:
            warning = f"Маршруты не посчитаны для {len(missing) - start} точек ({type(exc).__name__}: {exc})"
            break
        route_cache.put_routes(conn, fresh, now=now)
        conn.commit()
        cached.update(fresh)
    return {point: cached[point] for point in points if point in cached}, warning
