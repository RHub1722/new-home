"""Компоненты балла участка (спека §6): каждый даёт 0–100 и короткое пояснение по-русски."""

from dataclasses import dataclass

from homealize.config import ScoringWeights
from homealize.models import Route
from homealize.settlements import SettlementRef

IN_CITY_ENTRY = "в черте города"

CATEGORY_LABELS = {
    "supermarket": "супермаркет",
    "school": "школа",
    "kindergarten": "детсад",
    "pharmacy": "аптека",
    "bus_stop": "остановка",
    "clinic": "медпункт",
}
UTILITY_NAMES = {"electricity": "свет", "gas": "газ", "water": "вода", "sewerage": "канализация"}
UTILITY_MISSING = {"electricity": "электричества", "gas": "газа", "water": "воды", "sewerage": "канализации"}

FLAG_LABELS = {
    "suspicious_price": "🔍 подозрительно дёшево — проверить вручную",
    "coords_approx": "📍 координаты приблизительные (центр села)",
    "no_coords": "📍 нет координат",
    "settlement_unknown": "село не изучено",
    **{f"no_{utility}": f"⚠ нет {name} (не указано в объявлении)" for utility, name in UTILITY_MISSING.items()},
}
EXCLUSION_LABELS = {
    "no_coords": "нет координат",
    "no_route": "маршрут до города не найден",
    "too_far": "слишком далеко от города",
    "no_price_eur": "цену не удалось перевести в евро",
    "over_budget": "дороже бюджета",
    "suspicious_price": "подозрительно дёшево",
    "duplicate": "дубликат более дешёвого объявления",
}


@dataclass(frozen=True)
class Component:
    score: float
    note: str


def fraction(value: float, best: float, worst: float) -> float:
    """1 — значение на уровне best или лучше, 0 — на уровне worst или хуже, между ними линейно."""
    t = (value - worst) / (best - worst)
    return min(1.0, max(0.0, t))


def price_component(
    price_per_sot: float | None,
    median: float | None,
    *,
    label: str,
    n: int,
    full_discount: float,
    suspicious: bool,
) -> Component:
    if suspicious:
        return Component(
            50.0,
            f"Цена 50: подозрительно дёшево — {price_per_sot:.0f} €/сот. при медиане {median:.0f} €/сот., "
            "проверьте вручную",
        )
    if price_per_sot is None or not median:
        return Component(50.0, "Цена 50: не с чем сравнить")
    deviation = (price_per_sot - median) / median
    score = min(100.0, max(0.0, 50.0 - 50.0 * deviation / full_discount))
    percent = round(abs(deviation) * 100)
    if percent == 0:
        comparison = "на уровне медианы"
    else:
        comparison = f"на {percent}% {'дешевле' if deviation < 0 else 'дороже'} медианы"
    return Component(score, f"Цена {score:.0f}: {comparison} ({median:.0f} €/сот., {label}, объявлений: {n})")


def route_component(route: Route | None, *, best_min: float, worst_min: float) -> Component:
    if route is None:
        return Component(0.0, "Дорога 0: маршрут до города не найден")
    if route.entry == IN_CITY_ENTRY:
        return Component(100.0, "Дорога 100: участок в черте Кишинёва")
    score = 100.0 * fraction(route.minutes, best_min, worst_min)
    return Component(score, f"Дорога {score:.0f}: {route.minutes:.0f} мин до въезда «{route.entry}» ({route.km:.1f} км)")


def infra_component(
    nearest: dict[str, float | None],
    *,
    weights: dict[str, float],
    full_m: float,
    zero_m: float,
    data_available: bool = True,
) -> Component:
    if not data_available:  # POI ещё не скачаны: «нет ближе 1,5 км» было бы неправдой
        return Component(0.0, "Инфраструктура не оценена: нет данных OpenStreetMap")
    total_weight = sum(weights.values())
    earned = 0.0
    parts = []
    for category, weight in weights.items():
        distance = nearest.get(category)
        label = CATEGORY_LABELS[category]
        if distance is not None and distance < zero_m:
            earned += weight * fraction(distance, full_m, zero_m)
            parts.append(f"{label} {distance:.0f} м")
        else:
            parts.append(f"{label} нет ближе {_km(zero_m)} км")
    score = 100.0 * earned / total_weight if total_weight else 0.0
    return Component(score, f"Инфраструктура {score:.0f}: " + ", ".join(parts))


def utilities_component(values: dict[str, object], *, weights: dict[str, float]) -> tuple[Component, list[str]]:
    present = [utility for utility in weights if values.get(utility)]
    missing = [utility for utility in weights if not values.get(utility)]
    total = sum(weights.values())
    score = 100.0 * sum(weights[utility] for utility in present) / total if total else 0.0
    note = f"Коммуникации {score:.0f}: "
    note += f"есть {', '.join(UTILITY_NAMES[u] for u in present)}" if present else "ничего не указано"
    if missing:
        note += f"; нет {', '.join(UTILITY_MISSING[u] for u in missing)}"
    return Component(score, note), [f"no_{utility}" for utility in missing]


def settlement_component(ref: SettlementRef | None, *, unknown_score: float) -> tuple[Component, list[str]]:
    if ref is None:
        return Component(unknown_score, f"Село {unknown_score:.0f}: нет в справочнике, не изучено"), [
            "settlement_unknown"
        ]
    return Component(ref.score, f"Село {ref.score:.0f}: «{ref.potential}» по справочнику"), []


def weighted_total(components: dict[str, Component], weights: ScoringWeights) -> float:
    return round(
        components["price"].score * weights.price
        + components["route"].score * weights.route
        + components["infrastructure"].score * weights.infrastructure
        + components["utilities"].score * weights.utilities
        + components["settlement"].score * weights.settlement,
        1,
    )


def _km(meters: float) -> str:
    return f"{meters / 1000:g}".replace(".", ",")
