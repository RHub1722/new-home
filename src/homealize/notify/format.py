"""Тексты сообщений Telegram (HTML, спека §7.1). Всё, что пришло с сайта, экранируется."""

import json
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from homealize.export.excel import AUTHOR_LABELS
from homealize.scoring.components import EXCLUSION_LABELS, FLAG_LABELS, IN_CITY_ENTRY

Row = Any  # строка scores.load_scored / get_scored (в тестах — dict с теми же ключами)

COMPONENT_NAMES = {
    "price": "цена",
    "route": "дорога",
    "infrastructure": "инфраструктура",
    "utilities": "коммуникации",
    "settlement": "село",
}
NO_SETTLEMENT = "село не указано"
SHORT_LIMIT = 200


def money(value: float | None, currency: str | None = "EUR") -> str:
    """«32 000 €», «700 000 MDL»."""
    if value is None:
        return "цена не указана"
    unit = "€" if currency in (None, "EUR") else currency
    return f"{value:,.0f}".replace(",", " ") + f" {unit}"


def decimal(value: float, digits: int = 1) -> str:
    """Десятичная запятая: 9,4."""
    return f"{value:.{digits}f}".replace(".", ",")


def area(value: float | None) -> str:
    if value is None:
        return "площадь не указана"
    return f"{decimal(value).removesuffix(',0')} сот."


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 участок, 3 участка, 5 участков."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def short(text: str, limit: int = SHORT_LIMIT) -> str:
    """Первая строка, не длиннее limit символов."""
    first = next(iter(str(text).splitlines()), "")
    return first if len(first) <= limit else first[: limit - 1] + "…"


def local_time(value: str | datetime, zone: ZoneInfo, pattern: str = "%d.%m %H:%M") -> str:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(zone).strftime(pattern)


def deviation(price_per_sot: float | None, median: float | None) -> str | None:
    if not price_per_sot or not median:
        return None
    percent = round((price_per_sot / median - 1) * 100)
    if percent == 0:
        return "на уровне медианы"
    return f"на {abs(percent)}% {'дешевле' if percent < 0 else 'дороже'} медианы"


def ad_command(listing_id: str) -> str:
    """Команда-ссылка на карточку: в Telegram по ней можно нажать."""
    return f"/ad_{listing_id}"


def osm_url(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=16/{lat:.5f}/{lon:.5f}"


def first_photo(row: Row) -> str | None:
    images = json.loads(row["images"] or "[]")
    return images[0] if images else None


def star(fav: bool) -> str:
    return "★ " if fav else ""


def settlement_name(row: Row) -> str:
    return escape(row["settlement"] or NO_SETTLEMENT)


def place(row: Row) -> str:
    """«Сынжера (Новые Анены)»."""
    name = settlement_name(row)
    return f"{name} ({escape(row['region'])})" if row["region"] else name


def headline(row: Row) -> str:
    """«балл 78 · место 3»."""
    text = f"балл {row['total']:.0f}"
    return f"{text} · место {row['rank']}" if row["rank"] is not None else text


def price_line(row: Row) -> str:
    """«32 000 € · 4 000 €/сот. · на 17% дешевле медианы»."""
    price = money(row["price_original"], row["currency"])
    if row["currency"] not in (None, "EUR") and row["price_eur"] is not None:
        price += f" (≈ {money(row['price_eur'])})"
    parts = [price]
    if row["price_per_sot_eur"] is not None:
        parts.append(f"{money(row['price_per_sot_eur'])}/сот.")
    note = deviation(row["price_per_sot_eur"], row["market_median_eur_per_sot"])
    if note:
        parts.append(note)
    return " · ".join(parts)


def route_line(row: Row) -> str:
    if row["nearest_entry"] == IN_CITY_ENTRY:
        return "🏙 в черте Кишинёва"
    if row["route_min"] is None:
        return "🚗 маршрут до города не найден"
    return f"🚗 {row['route_min']:.0f} мин до въезда «{escape(row['nearest_entry'])}», {decimal(row['route_km'])} км"


def flag_lines(row: Row) -> list[str]:
    return [escape(FLAG_LABELS.get(flag, flag)) for flag in json.loads(row["flags"])]


def breakdown(row: Row) -> str:
    """«цена 67 · дорога 80 · …»."""
    scores = json.loads(row["components"])
    return " · ".join(f"{label} {scores[name]:.0f}" for name, label in COMPONENT_NAMES.items() if name in scores)


def links(row: Row) -> str:
    parts = [f'<a href="{escape(row["url"])}">999.md</a>']
    if row["lat"] is not None and row["lon"] is not None:
        label = "карта (центр села)" if row["coords_approx"] else "карта"
        parts.append(f'<a href="{escape(osm_url(row["lat"], row["lon"]))}">{label}</a>')
    parts.append(ad_command(row["listing_id"]))
    return " · ".join(parts)


def listing_line(row: Row, *, fav: bool, note: str = "") -> str:
    """Строка списка: «3. ★ 78 · Сынжера, 8 сот., 32 000 € (−17%), 11 мин /ad_123»."""
    rank = f"{row['rank']}. " if row["rank"] is not None else ""
    price = money(row["price_eur"])
    percent = _short_deviation(row["price_per_sot_eur"], row["market_median_eur_per_sot"])
    if percent:
        price += f" ({percent})"
    minutes = f", {row['route_min']:.0f} мин" if row["route_min"] is not None else ""
    tail = f" · {note}" if note else ""
    return (
        f"{rank}{star(fav)}{row['total']:.0f} · {settlement_name(row)}, {area(row['area_sot'])}, "
        f"{price}{minutes}{tail} {ad_command(row['listing_id'])}"
    )


def new_listing(row: Row, *, fav: bool) -> str:
    return "\n".join([
        f"🆕 <b>Новый участок</b> · {headline(row)}",
        f"{star(fav)}{place(row)}, {area(row['area_sot'])}",
        price_line(row),
        route_line(row),
        *flag_lines(row),
        f"Баллы: {breakdown(row)}",
        links(row),
    ])


def price_drop(row: Row, *, old: str, new: str, percent: int, fav: bool) -> str:
    return "\n".join([
        f"📉 <b>Цена снижена</b>: было {old}, стало {new} (−{percent}%)",
        f"{star(fav)}{place(row)}, {area(row['area_sot'])} · {headline(row)}",
        price_line(row),
        route_line(row),
        links(row),
    ])


def card(row: Row, history: list, *, fav: bool, zone: ZoneInfo) -> str:
    """Карточка участка для /ad: всё, что известно об оценке."""
    explanations = json.loads(row["explanations"])
    if row["rank"] is not None:
        status = f"Место в рейтинге: {row['rank']}"
    else:
        reason = row["excluded_reason"]
        status = f"Не в рейтинге: {escape(EXCLUSION_LABELS.get(reason, reason or 'причина не указана'))}"
    lines = [
        f"{star(fav)}<b>{place(row)}</b>, {area(row['area_sot'])} · балл {row['total']:.0f}",
        status,
        price_line(row),
        route_line(row),
        *flag_lines(row),
        "",
        *[escape(explanations[name]) for name in COMPONENT_NAMES if name in explanations],
        "",
        f"Автор: {escape(AUTHOR_LABELS.get(row['author_type'], str(row['author_type'])))}",
        f"Впервые увидено: {local_time(row['first_seen_at'], zone, '%d.%m.%Y')}",
    ]
    prices = [money(entry["price_original"], entry["currency"]) for entry in history[-5:]]
    if len(prices) > 1:
        lines.append(f"История цены: {' → '.join(prices)}")
    lines.append(links(row))
    return "\n".join(lines)


def _short_deviation(price_per_sot: float | None, median: float | None) -> str:
    """«−17%», «+5%», «±0%»; пусто, если сравнивать не с чем."""
    if not price_per_sot or not median:
        return ""
    percent = round((price_per_sot / median - 1) * 100)
    return "±0%" if percent == 0 else f"{percent:+d}%".replace("-", "−")
