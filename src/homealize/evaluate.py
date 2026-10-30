"""Оценка этапа 2: подготовка внешних данных (POI, курс, маршруты) и пересчёт баллов."""

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from homealize.config import Settings
from homealize.enrich.currency import ensure_rates
from homealize.enrich.poi import PoiIndex, ensure_pois
from homealize.enrich.routing import OsrmRouter, ensure_routes
from homealize.scoring.engine import ScoreSummary, Services, rescore, route_points
from homealize.settlements import load_settlements

log = logging.getLogger(__name__)

USER_AGENT = "homealize/0.1 (personal land monitor)"


def build_http_client() -> httpx.Client:
    """Клиент для внешних сервисов оценки (не для 999.md)."""
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def evaluate(
    conn: sqlite3.Connection,
    settings: Settings,
    http: httpx.Client,
    *,
    now: datetime,
    force_poi: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> ScoreSummary:
    """Обновляет кэши внешних данных и пересчитывает снимок оценки. Ошибки сервисов становятся предупреждениями."""
    warnings: list[str] = []
    settlements = load_settlements(settings.scoring.settlements_path)
    pois, warning = ensure_pois(conn, http, settings.poi, now=now, force=force_poi)
    _note(warnings, warning)
    rates, warning = ensure_rates(conn, http, settings.currency, day=now.date())
    _note(warnings, warning)
    router = OsrmRouter(http, settings.routing)
    routes, warning = ensure_routes(conn, router, route_points(conn, settings, now=now), now=now, sleep=sleep)
    _note(warnings, warning)
    summary = rescore(conn, settings, Services(routes, PoiIndex(pois), rates, settlements), now=now)
    conn.commit()
    summary.warnings = warnings + summary.warnings
    log.info("Оценка: оценено %s, в рейтинге %s, лучший балл %s", summary.scored, summary.eligible,
             summary.best_total)
    return summary


def _note(warnings: list[str], warning: str | None) -> None:
    if warning:
        log.warning(warning)
        warnings.append(warning)
