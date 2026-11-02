"""Вкладка «Рынок» дашборда (спека §7.2): медиана €/сотку по сёлам и её динамика по неделям.

Медиана недели считается по объявлениям, которые были на сайте хотя бы раз за эту неделю, с их последней ценой
до конца недели. Учитываются все объявления, прошедшие фильтры типа и площади, включая дороже бюджета (спека §2).
Цены не в евро переводятся по последнему курсу BNM из кэша. Модуль не импортирует Streamlit.
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from homealize.enrich.currency import to_eur

ALL_PLACES = "Все сёла"


@dataclass(frozen=True)
class Place:
    settlement: str  # ALL_PLACES — все объявления вместе
    region: str | None

    @property
    def label(self) -> str:
        """«Сынжера (Новые Анены)»: одноимённые сёла различаются районом."""
        return f"{self.settlement} ({self.region})" if self.region else self.settlement


@dataclass(frozen=True)
class WeeklyMedian:
    week: date  # понедельник недели по местному времени
    place: Place
    n: int
    median: float  # €/сотку


@dataclass(frozen=True)
class MarketRow:
    place: Place
    n: int
    median: float
    change_percent: float | None  # к медиане compare_weeks недель назад; None — сравнить не с чем


def week_start(moment: datetime, zone: ZoneInfo) -> date:
    """Понедельник недели, в которую попадает момент, по местному времени."""
    day = moment.astimezone(zone).date()
    return day - timedelta(days=day.weekday())


def weekly_medians(
    conn: sqlite3.Connection,
    *,
    zone: ZoneInfo,
    rates: dict[str, float] | None,
    now: datetime,
    weeks: int,
    min_n: int,
) -> list[WeeklyMedian]:
    """Медианы за последние weeks недель, включая текущую; места, где объявлений меньше min_n, пропускаются."""
    listings = conn.execute(
        "SELECT id, settlement, region, area_sot, first_seen_at, last_seen_at FROM listings "
        "WHERE filter_reason IS NULL AND area_sot > 0"
    ).fetchall()
    history: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for entry in conn.execute(
        "SELECT listing_id, price_original, currency, observed_at FROM price_history "
        "WHERE price_original IS NOT NULL ORDER BY observed_at, id"
    ):
        history[entry["listing_id"]].append(entry)
    current = week_start(now, zone)
    result = []
    for offset in range(weeks - 1, -1, -1):
        week = current - timedelta(weeks=offset)
        start, end = _utc_midnight(week, zone), _utc_midnight(week + timedelta(weeks=1), zone)
        values: dict[Place, list[float]] = defaultdict(list)
        for listing in listings:
            if listing["first_seen_at"] >= end or listing["last_seen_at"] < start:
                continue
            price = _last_before(history.get(listing["id"], []), end)
            eur = to_eur(price["price_original"], price["currency"], rates) if price is not None else None
            if eur is None:
                continue
            per_sot = eur / listing["area_sot"]
            values[Place(ALL_PLACES, None)].append(per_sot)
            if listing["settlement"]:
                values[Place(listing["settlement"], listing["region"])].append(per_sot)
        result += [
            WeeklyMedian(week, place, len(items), round(median(items), 2))
            for place, items in sorted(values.items(), key=lambda item: item[0].label.casefold())
            if len(items) >= min_n
        ]
    return result


def market_table(weekly: list[WeeklyMedian], *, current: date, compare_weeks: int) -> list[MarketRow]:
    """Строки текущей недели: «Все сёла» первыми, дальше по числу объявлений."""
    by_key = {(item.week, item.place): item for item in weekly}
    base_week = current - timedelta(weeks=compare_weeks)
    rows = []
    for item in weekly:
        if item.week != current:
            continue
        base = by_key.get((base_week, item.place))
        change = round((item.median / base.median - 1) * 100, 1) if base is not None else None
        rows.append(MarketRow(item.place, item.n, item.median, change))
    return sorted(rows, key=lambda row: (row.place.settlement != ALL_PLACES, -row.n, row.place.label.casefold()))


def _utc_midnight(day: date, zone: ZoneInfo) -> str:
    """Полночь дня по местному времени — в ISO UTC, как времена в базе."""
    return datetime.combine(day, time(), tzinfo=zone).astimezone(UTC).isoformat()


def _last_before(entries: list[sqlite3.Row], end: str) -> sqlite3.Row | None:
    found = None
    for entry in entries:
        if entry["observed_at"] >= end:
            break
        found = entry
    return found
