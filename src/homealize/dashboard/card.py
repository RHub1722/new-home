"""Карточка участка в дашборде (спека §7.2) и отметки ★ / «скрыть».

Модуль не импортирует Streamlit. set_mark — единственная функция дашборда, которая пишет в базу.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from homealize.config import POI_CATEGORIES, Settings
from homealize.geo import haversine_m
from homealize.models import Poi
from homealize.runlock import LockBusyError, hold_lock
from homealize.scoring.components import CATEGORY_LABELS
from homealize.storage import marks, poi_cache, repo
from homealize.storage.scores import apply_hidden, get_scored

COMPONENT_LABELS = {
    "price": "Цена",
    "route": "Дорога",
    "infrastructure": "Инфраструктура",
    "utilities": "Коммуникации",
    "settlement": "Село",
}


class MarkBusyError(Exception):
    """Идёт прогон или база занята: отметка не поставлена, её нужно повторить через минуту."""


@dataclass(frozen=True)
class ComponentLine:
    name: str
    label: str
    score: float
    weight: float
    points: float  # вклад в итоговый балл: score × weight
    note: str  # пояснение из оценки


@dataclass(frozen=True)
class PricePoint:
    observed_at: datetime
    price: float
    currency: str | None


@dataclass(frozen=True)
class NearbyPoi:
    category: str
    label: str  # «супермаркет»
    name: str | None
    distance_m: int


@dataclass(frozen=True)
class Card:
    row: sqlite3.Row  # строка scores.get_scored
    fav: bool
    hidden: bool
    components: list[ComponentLine]
    history: list[PricePoint]
    nearby: list[NearbyPoi]
    photos: list[str]


def load_card(conn: sqlite3.Connection, settings: Settings, listing_id: str) -> Card | None:
    """Всё для карточки; None — объявления нет в снимке оценки."""
    row = get_scored(conn, listing_id)
    if row is None:
        return None
    return Card(
        row=row,
        fav=listing_id in marks.marked(conn, marks.FAV),
        hidden=listing_id in marks.marked(conn, marks.HIDDEN),
        components=component_lines(row, settings),
        history=price_points(conn, listing_id),
        nearby=nearby_pois(conn, row["lat"], row["lon"], max_m=settings.scoring.poi_zero_m),
        photos=json.loads(row["images"] or "[]")[: settings.dashboard.photos],
    )


def component_lines(row: sqlite3.Row, settings: Settings) -> list[ComponentLine]:
    """Разбивка балла по компонентам с весами из настроек."""
    scores = json.loads(row["components"])
    notes = json.loads(row["explanations"])
    weights = settings.scoring.weights
    lines = []
    for name, label in COMPONENT_LABELS.items():
        if name not in scores:
            continue
        weight = getattr(weights, name)
        lines.append(
            ComponentLine(name, label, scores[name], weight, round(scores[name] * weight, 1), notes.get(name, ""))
        )
    return lines


def price_points(conn: sqlite3.Connection, listing_id: str) -> list[PricePoint]:
    return [
        PricePoint(datetime.fromisoformat(entry["observed_at"]), entry["price_original"], entry["currency"])
        for entry in repo.get_price_history(conn, listing_id)
        if entry["price_original"] is not None
    ]


def nearby_pois(conn: sqlite3.Connection, lat: float | None, lon: float | None, *, max_m: float) -> list[NearbyPoi]:
    """Ближайший объект каждой категории не дальше max_m, в порядке POI_CATEGORIES."""
    if lat is None or lon is None:
        return []
    best: dict[str, tuple[float, Poi]] = {}
    for poi in poi_cache.load_poi(conn):
        distance = haversine_m(lat, lon, poi.lat, poi.lon)
        if distance <= max_m and (poi.category not in best or distance < best[poi.category][0]):
            best[poi.category] = (distance, poi)
    nearby = []
    for category in POI_CATEGORIES:
        if category in best:
            distance, poi = best[category]
            label = CATEGORY_LABELS.get(category, category)
            nearby.append(NearbyPoi(category, label, poi.name or poi.brand, round(distance)))
    return nearby


def set_mark(
    conn: sqlite3.Connection, settings: Settings, listing_id: str, mark: str, *, on: bool, now: datetime
) -> bool:
    """Ставит (on=True) или снимает ★ / «скрыть»; «скрыть» сразу меняет места, как /hide в боте.

    Пишет под блокировкой прогона: пересчёт баллов посреди прогона мог бы затереть отметку.
    True — отметка изменилась. MarkBusyError — идёт прогон или базу занял другой процесс (SQLITE_BUSY/LOCKED);
    остальные ошибки SQLite пробрасываются как есть.
    """
    if mark not in (marks.FAV, marks.HIDDEN):
        raise ValueError(f"Неизвестная отметка: {mark}")
    try:
        with hold_lock(settings.storage.run_lock_path):
            try:
                if on:
                    changed = marks.add_mark(conn, listing_id, mark, now=now)
                else:
                    changed = marks.remove_mark(conn, listing_id, mark)
                if mark == marks.HIDDEN:
                    apply_hidden(conn, marks.marked(conn, marks.HIDDEN))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except LockBusyError:
        raise MarkBusyError("идёт прогон") from None
    except sqlite3.OperationalError as exc:
        if (exc.sqlite_errorcode & 0xFF) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):  # другое — не прячем
            raise
        raise MarkBusyError(str(exc)) from exc  # «database is locked»: в базу пишет другой процесс
    return changed
