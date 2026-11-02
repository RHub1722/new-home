"""Данные вкладок «Топ» и «Карта» дашборда (спека §7.2): строки рейтинга, фильтры, метки, цвет балла.

Модуль не импортирует Streamlit: здесь только чтение базы и чистые функции.
"""

import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from homealize.config import UTILITIES
from homealize.notify.format import NO_SETTLEMENT, plural
from homealize.scoring.components import EXCLUSION_LABELS, FLAG_LABELS
from homealize.storage import marks
from homealize.storage.scores import load_scored

# Причины исключения, которые можно показать переключателями. Остальные (далеко, нет маршрута или координат,
# дубликат, цена не в евро) дашборд не показывает: они есть в Excel на листе «Все активные».
OPTIONAL_REASONS = frozenset({"hidden", "suspicious_price", "over_budget"})
LISTINGS_LAYER = "listings"  # id слоя участков на карте: по нему находится выбранная точка
# Цвет балла как в Excel: 0 — красный, 50 — жёлтый, 100 — зелёный.
_SCALE = ((0.0, (0xF8, 0x69, 0x6B)), (50.0, (0xFF, 0xEB, 0x84)), (100.0, (0x63, 0xBE, 0x7B)))


@dataclass(frozen=True)
class ListingView:
    listing_id: str
    rank: int | None
    total: float
    settlement: str  # NO_SETTLEMENT, если село не указано
    region: str | None
    area_sot: float | None
    price_eur: float | None
    price_per_sot_eur: float | None
    deviation_percent: int | None  # к медиане рынка: −17 — на 17% дешевле
    route_min: float | None
    lat: float | None
    lon: float | None
    utilities: frozenset[str]  # из UTILITIES: что указано в объявлении
    flags: tuple[str, ...]
    excluded_reason: str | None
    fav: bool
    hidden: bool
    group_extra: int  # сколько ещё объявлений в той же группе дубликатов
    url: str


@dataclass(frozen=True)
class Filters:
    """Фильтры боковой панели. None и пустые множества — без ограничения."""

    price: tuple[float, float] | None = None  # €, включительно
    area: tuple[float, float] | None = None  # сотки, включительно
    max_minutes: float | None = None
    utilities: frozenset[str] = frozenset()  # обязательные коммуникации
    settlements: frozenset[str] = frozenset()
    only_favs: bool = False
    show_hidden: bool = False
    show_suspicious: bool = False
    show_over_budget: bool = False


@dataclass(frozen=True)
class Bounds:
    """Границы ползунков: по всем строкам, которые можно показать хоть каким-то переключателем."""

    price: tuple[float, float]
    area: tuple[float, float]
    max_minutes: float


def load_listings(conn: sqlite3.Connection) -> list[ListingView]:
    """Все строки снимка оценки в порядке рейтинга: сначала места, потом остальные по баллу."""
    rows = load_scored(conn)
    favs = marks.marked(conn, marks.FAV)
    hidden = marks.marked(conn, marks.HIDDEN)
    group_sizes = Counter(row["duplicate_group"] for row in rows if row["duplicate_group"] is not None)
    return [_view(row, favs, hidden, group_sizes) for row in rows]


def candidates(rows: Iterable[ListingView]) -> list[ListingView]:
    """Строки, которые дашборд может показать: в рейтинге или исключённые по причинам из OPTIONAL_REASONS."""
    return [row for row in rows if row.excluded_reason is None or row.excluded_reason in OPTIONAL_REASONS]


def apply_filters(rows: Iterable[ListingView], filters: Filters) -> list[ListingView]:
    """Строки под фильтры; порядок сохраняется."""
    allowed: set[str | None] = {None}
    if filters.show_hidden:
        allowed.add("hidden")
    if filters.show_suspicious:
        allowed.add("suspicious_price")
    if filters.show_over_budget:
        allowed.add("over_budget")
    return [row for row in rows if row.excluded_reason in allowed and _passes(row, filters)]


def bounds(rows: Iterable[ListingView]) -> Bounds | None:
    """Границы ползунков по candidates(rows); None — показывать нечего."""
    shown = candidates(rows)
    prices = [row.price_eur for row in shown if row.price_eur is not None]
    areas = [row.area_sot for row in shown if row.area_sot is not None]
    minutes = [row.route_min for row in shown if row.route_min is not None]
    if not prices or not areas or not minutes:
        return None
    return Bounds(
        price=(math.floor(min(prices)), math.ceil(max(prices))),
        area=(math.floor(min(areas) * 2) / 2, math.ceil(max(areas) * 2) / 2),  # с шагом 0,5 сотки
        max_minutes=float(math.ceil(max(minutes))),
    )


def settlement_options(rows: Iterable[ListingView]) -> list[str]:
    """Сёла для фильтра: по всем строкам, которые можно показать, по алфавиту."""
    return sorted({row.settlement for row in candidates(rows)}, key=str.casefold)


def labels(row: ListingView) -> str:
    """Колонка «Метки»: причина исключения, другие объявления того же участка, метки оценки."""
    parts = []
    if row.excluded_reason:
        parts.append(EXCLUSION_LABELS.get(row.excluded_reason, row.excluded_reason))
    if row.group_extra:
        word = plural(row.group_extra, "объявление", "объявления", "объявлений")
        parts.append(f"ещё {row.group_extra} {word} этого участка")
    parts += [FLAG_LABELS.get(flag, flag) for flag in row.flags]
    return "; ".join(parts)


def score_color(total: float) -> tuple[int, int, int]:
    """Цвет точки на карте по баллу: та же шкала, что в Excel."""
    value = min(100.0, max(0.0, total))
    for (low, low_rgb), (high, high_rgb) in zip(_SCALE, _SCALE[1:]):
        if value <= high:
            t = (value - low) / (high - low)
            red, green, blue = (round(a + (b - a) * t) for a, b in zip(low_rgb, high_rgb))
            return red, green, blue
    return _SCALE[-1][1]


def picked_listing_id(selection: Mapping | None, layer: str = LISTINGS_LAYER) -> str | None:
    """id участка, выбранного на карте (выбор st.pydeck_chart: {"objects": {слой: [данные точки]}})."""
    if not selection:
        return None
    picked = (selection.get("objects") or {}).get(layer) or []
    return str(picked[0]["id"]) if picked and "id" in picked[0] else None


def _passes(row: ListingView, filters: Filters) -> bool:
    if row.hidden and not filters.show_hidden:
        return False  # скрытое «дороже бюджета» тоже прячется
    if filters.only_favs and not row.fav:
        return False
    if filters.price is not None and not _within(row.price_eur, filters.price):
        return False
    if filters.area is not None and not _within(row.area_sot, filters.area):
        return False
    if filters.max_minutes is not None and (row.route_min is None or row.route_min > filters.max_minutes):
        return False
    if not filters.utilities <= row.utilities:
        return False
    return not filters.settlements or row.settlement in filters.settlements


def _within(value: float | None, limits: tuple[float, float]) -> bool:
    return value is not None and limits[0] <= value <= limits[1]


def _view(row: sqlite3.Row, favs: set[str], hidden: set[str], group_sizes: Counter) -> ListingView:
    price_per_sot, market = row["price_per_sot_eur"], row["market_median_eur_per_sot"]
    group = row["duplicate_group"]
    return ListingView(
        listing_id=row["listing_id"],
        rank=row["rank"],
        total=row["total"],
        settlement=row["settlement"] or NO_SETTLEMENT,
        region=row["region"],
        area_sot=row["area_sot"],
        price_eur=row["price_eur"],
        price_per_sot_eur=price_per_sot,
        deviation_percent=round((price_per_sot / market - 1) * 100) if price_per_sot and market else None,
        route_min=row["route_min"],
        lat=row["lat"],
        lon=row["lon"],
        utilities=frozenset(name for name in UTILITIES if row[f"has_{name}"]),
        flags=tuple(json.loads(row["flags"])),
        excluded_reason=row["excluded_reason"],
        fav=row["listing_id"] in favs,
        hidden=row["listing_id"] in hidden,
        group_extra=group_sizes[group] - 1 if group is not None else 0,
        url=row["url"],
    )
