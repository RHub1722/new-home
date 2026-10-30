"""Рынок: медианы €/сотку по уровням, «подозрительно дёшево», группы дубликатов (спека §5, §6.1)."""

from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from homealize.geo import haversine_m


@dataclass(frozen=True)
class MarketItem:
    listing_id: str
    settlement_id: int | None
    route_km: float | None
    price_per_sot: float


@dataclass(frozen=True)
class Median:
    value: float
    level: str  # settlement | route_band | region
    n: int
    label: str  # для пояснения: «село Сынжера», «участки в 8–14 км от города», «весь регион»


class MarketMedians:
    """Медиана по селу (n ≥ min_settlement), иначе по участкам ±band_km (n ≥ min_route_band), иначе по региону."""

    def __init__(
        self,
        items: list[MarketItem],
        *,
        min_settlement: int,
        min_route_band: int,
        band_km: float,
        settlement_names: dict[int, str],
    ) -> None:
        self._items = list(items)
        self._min_settlement = min_settlement
        self._min_route_band = min_route_band
        self._band_km = band_km
        self._names = settlement_names
        self._by_settlement: dict[int, list[float]] = defaultdict(list)
        for item in self._items:
            if item.settlement_id is not None:
                self._by_settlement[item.settlement_id].append(item.price_per_sot)

    def for_listing(self, settlement_id: int | None, route_km: float | None) -> Median | None:
        values = self._by_settlement.get(settlement_id, []) if settlement_id is not None else []
        if len(values) >= self._min_settlement:
            name = self._names.get(settlement_id, str(settlement_id))
            return Median(round(median(values), 2), "settlement", len(values), f"село {name}")
        if route_km is not None:
            band = [
                item.price_per_sot
                for item in self._items
                if item.route_km is not None and abs(item.route_km - route_km) <= self._band_km
            ]
            if len(band) >= self._min_route_band:
                low, high = max(0.0, route_km - self._band_km), route_km + self._band_km
                return Median(round(median(band), 2), "route_band", len(band),
                              f"участки в {low:.0f}–{high:.0f} км от города")
        if self._items:
            return Median(round(median(item.price_per_sot for item in self._items), 2), "region",
                          len(self._items), "весь регион")
        return None


def is_suspicious(price_per_sot: float | None, market: Median | None, ratio: float) -> bool:
    return price_per_sot is not None and market is not None and price_per_sot < ratio * market.value


@dataclass(frozen=True)
class DupItem:
    listing_id: str
    area_sot: float
    lat: float
    lon: float
    price_eur: float


def group_duplicates(
    items: list[DupItem], *, distance_m: float, price_tolerance: float
) -> dict[str, tuple[str, bool]]:
    """Одинаковая площадь, ≤ distance_m и цена в пределах price_tolerance — одна группа.

    Возвращает id → (id группы = наименьший id, главное ли объявление = самое дешёвое).
    """
    parent = {item.listing_id: item.listing_id for item in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_area: dict[float, list[DupItem]] = defaultdict(list)
    for item in items:
        by_area[item.area_sot].append(item)
    for group in by_area.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if haversine_m(a.lat, a.lon, b.lat, b.lon) > distance_m:
                    continue
                low = min(a.price_eur, b.price_eur)
                if low <= 0 or abs(a.price_eur - b.price_eur) / low > price_tolerance:
                    continue
                root_a, root_b = find(a.listing_id), find(b.listing_id)
                if root_a != root_b:
                    parent[max(root_a, root_b)] = min(root_a, root_b)

    members: dict[str, list[DupItem]] = defaultdict(list)
    for item in items:
        members[find(item.listing_id)].append(item)
    result: dict[str, tuple[str, bool]] = {}
    for group in members.values():
        if len(group) < 2:
            continue
        primary = min(group, key=lambda item: (item.price_eur, item.listing_id))
        group_id = min(item.listing_id for item in group)
        for item in group:
            result[item.listing_id] = (group_id, item.listing_id == primary.listing_id)
    return result
