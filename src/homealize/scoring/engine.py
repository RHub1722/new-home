"""Оценка участков: местоположение, рыночные медианы, компоненты балла, исключения и рейтинг (спека §6, §12).

Движок не ходит в сеть: маршруты, POI, курс и справочник сёл передаются через Services.
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median

from homealize.config import ScoringSettings, Settings
from homealize.enrich.currency import to_eur
from homealize.enrich.poi import PoiIndex
from homealize.enrich.routing import round_key
from homealize.models import Route
from homealize.scoring.components import (
    IN_CITY_ENTRY,
    Component,
    infra_component,
    price_component,
    route_component,
    settlement_component,
    utilities_component,
    weighted_total,
)
from homealize.scoring.market import DupItem, MarketItem, MarketMedians, Median, group_duplicates, is_suspicious
from homealize.settlements import SettlementRef
from homealize.storage import marks
from homealize.storage.scores import assign_ranks, replace_scores


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    approx: bool  # True — центр села, а не точка из объявления


@dataclass(frozen=True)
class Services:
    routes: dict[tuple[float, float], Route | None]
    poi_index: PoiIndex
    rates: dict[str, float] | None
    settlements: dict[int, SettlementRef]


@dataclass
class ScoreSummary:
    scored: int = 0  # активных объявлений с посчитанным баллом
    eligible: int = 0  # из них в рейтинге
    best_total: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Candidate:
    row: sqlite3.Row
    location: Location | None
    route: Route | None
    in_city: bool
    price_eur: float | None
    price_per_sot: float | None


def load_rows(conn: sqlite3.Connection, settings: Settings, *, now: datetime) -> list[sqlite3.Row]:
    """Активные объявления и снятые за median_removed_days (для медиан), прошедшие фильтры этапа 1."""
    cutoff = (now.astimezone(UTC) - timedelta(days=settings.scoring.median_removed_days)).isoformat()
    return conn.execute(
        "SELECT * FROM listings WHERE filter_reason IS NULL "
        "AND (status = 'active' OR (status = 'removed' AND last_seen_at >= ?)) ORDER BY id",
        (cutoff,),
    ).fetchall()


def locate(rows: list[sqlite3.Row]) -> dict[str, Location | None]:
    """Точка из объявления, иначе медиана координат объявлений того же села, иначе None."""
    points: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        if row["lat"] is not None and row["lon"] is not None and row["settlement_id"] is not None:
            points[row["settlement_id"]].append((row["lat"], row["lon"]))
    centres = {
        settlement: (median(p[0] for p in pts), median(p[1] for p in pts)) for settlement, pts in points.items()
    }
    locations: dict[str, Location | None] = {}
    for row in rows:
        if row["lat"] is not None and row["lon"] is not None:
            locations[row["id"]] = Location(row["lat"], row["lon"], approx=False)
        elif row["settlement_id"] in centres:
            lat, lon = centres[row["settlement_id"]]
            locations[row["id"]] = Location(lat, lon, approx=True)
        else:
            locations[row["id"]] = None
    return locations


def route_points(conn: sqlite3.Connection, settings: Settings, *, now: datetime) -> set[tuple[float, float]]:
    """Точки (round_key), для которых оценке нужен маршрут OSRM."""
    rows = load_rows(conn, settings, now=now)
    locations = locate(rows)
    city = set(settings.routing.city_settlement_ids)
    return {
        round_key(locations[row["id"]].lat, locations[row["id"]].lon)
        for row in rows
        if locations[row["id"]] is not None and row["settlement_id"] not in city
    }


def rescore(conn: sqlite3.Connection, settings: Settings, services: Services, *, now: datetime) -> ScoreSummary:
    scoring = settings.scoring
    rows = load_rows(conn, settings, now=now)
    locations = locate(rows)
    city = set(settings.routing.city_settlement_ids)
    candidates = [_candidate(row, locations[row["id"]], services, city) for row in rows]
    active = [c for c in candidates if c.row["status"] == "active"]
    hidden = marks.marked(conn, marks.HIDDEN)

    # Группы дубликатов строятся и по недавно снятым: снятая копия не должна дважды попадать в медиану.
    groups = group_duplicates(
        [
            DupItem(c.row["id"], c.row["area_sot"], c.location.lat, c.location.lon, c.price_eur)
            for c in candidates
            if c.location is not None and not c.location.approx and c.price_eur is not None
        ],
        distance_m=scoring.duplicate_distance_m,
        price_tolerance=scoring.duplicate_price_tolerance,
    )
    primaries = _primaries(candidates, groups)

    names = {c.row["settlement_id"]: c.row["settlement"] for c in candidates if c.row["settlement_id"] is not None}
    max_km = settings.routing.max_route_km
    market = [
        c for c in candidates
        if c.route is not None and c.route.km <= max_km and c.price_per_sot is not None and primaries[c.row["id"]]
    ]
    first_pass = _medians(market, scoring, names)
    clean = [
        c for c in market
        if not is_suspicious(c.price_per_sot, first_pass.for_listing(c.row["settlement_id"], c.route.km),
                             scoring.suspicious_ratio)
    ]
    medians = _medians(clean, scoring, names)

    computed_at = now.astimezone(UTC).isoformat()
    records = []
    for c in active:
        market_median = medians.for_listing(c.row["settlement_id"], c.route.km if c.route else None)
        suspicious = is_suspicious(c.price_per_sot, market_median, scoring.suspicious_ratio)
        group = groups.get(c.row["id"], (None, True))[0]
        primary = primaries[c.row["id"]]
        nearest = services.poi_index.nearest_m(c.location.lat, c.location.lon) if c.location else {}
        components, flags = _components(c, market_median, suspicious, nearest, services, scoring)
        records.append({
            "listing_id": c.row["id"],
            "computed_at": computed_at,
            "price_eur": c.price_eur,
            "price_per_sot_eur": c.price_per_sot,
            "lat": c.location.lat if c.location else None,
            "lon": c.location.lon if c.location else None,
            "coords_approx": bool(c.location and c.location.approx),
            "route_km": c.route.km if c.route else None,
            "route_min": c.route.minutes if c.route else None,
            "nearest_entry": c.route.entry if c.route else None,
            "poi_nearest_m": json.dumps(nearest, ensure_ascii=False),
            "market_median_eur_per_sot": market_median.value if market_median else None,
            "median_level": market_median.level if market_median else None,
            "median_n": market_median.n if market_median else None,
            "duplicate_group": group,
            "is_group_primary": primary,
            "components": json.dumps({name: comp.score for name, comp in components.items()}),
            "explanations": json.dumps({name: comp.note for name, comp in components.items()}, ensure_ascii=False),
            "flags": json.dumps(flags, ensure_ascii=False),
            "total": weighted_total(components, scoring.weights),
            "excluded_reason": _excluded_reason(c, suspicious, primary, settings, hidden),
            "rank": None,
        })

    eligible = [r for r in records if r["excluded_reason"] is None]
    replace_scores(conn, records)
    assign_ranks(conn)  # места считает снимок: так же их пересчитывают /hide и /unhide
    return ScoreSummary(
        scored=len(records),
        eligible=len(eligible),
        best_total=max((r["total"] for r in eligible), default=None),
    )


def _candidate(row: sqlite3.Row, location: Location | None, services: Services, city: set[int]) -> _Candidate:
    in_city = row["settlement_id"] in city
    if in_city:
        route = Route(km=0.0, minutes=0.0, entry=IN_CITY_ENTRY)
    elif location is None:
        route = None
    else:
        route = services.routes.get(round_key(location.lat, location.lon))
    price_eur = to_eur(row["price_original"], row["currency"], services.rates)
    area = row["area_sot"]
    price_per_sot = round(price_eur / area, 2) if price_eur is not None and area else None
    return _Candidate(row, location, route, in_city, price_eur, price_per_sot)


def _primaries(candidates: list[_Candidate], groups: dict[str, tuple[str, bool]]) -> dict[str, bool]:
    """id -> главное ли объявление: вне групп все главные, в группе одно (активное важнее снятой, потом дешевле)."""
    members: dict[str, list[_Candidate]] = defaultdict(list)
    for c in candidates:
        if c.row["id"] in groups:
            members[groups[c.row["id"]][0]].append(c)
    primaries = {c.row["id"]: True for c in candidates}
    for group in members.values():
        best = min(group, key=lambda c: (c.row["status"] != "active", c.price_eur, c.row["id"]))
        for c in group:
            primaries[c.row["id"]] = c is best
    return primaries


def _medians(candidates: list[_Candidate], scoring: ScoringSettings, names: dict[int, str]) -> MarketMedians:
    return MarketMedians(
        [MarketItem(c.row["id"], c.row["settlement_id"], c.route.km, c.price_per_sot) for c in candidates],
        min_settlement=scoring.median_min_settlement,
        min_route_band=scoring.median_min_route_band,
        band_km=scoring.median_route_band_km,
        settlement_names=names,
    )


def _components(
    c: _Candidate,
    market_median: Median | None,
    suspicious: bool,
    nearest: dict[str, int | None],
    services: Services,
    scoring: ScoringSettings,
) -> tuple[dict[str, Component], list[str]]:
    price = price_component(
        c.price_per_sot,
        market_median.value if market_median else None,
        label=market_median.label if market_median else "",
        n=market_median.n if market_median else 0,
        full_discount=scoring.price_full_discount,
        suspicious=suspicious,
    )
    route = route_component(c.route, best_min=scoring.route_best_min, worst_min=scoring.route_worst_min)
    infra = infra_component(
        nearest,
        weights=scoring.poi_weights,
        full_m=scoring.poi_full_m,
        zero_m=scoring.poi_zero_m,
        data_available=services.poi_index.has_objects,
    )
    utilities, utility_flags = utilities_component(
        {utility: c.row[f"has_{utility}"] for utility in scoring.utility_weights}, weights=scoring.utility_weights
    )
    settlement, settlement_flags = settlement_component(
        services.settlements.get(c.row["settlement_id"]), unknown_score=scoring.unknown_settlement_score
    )
    flags = [*utility_flags, *settlement_flags]
    if suspicious:
        flags.append("suspicious_price")
    if c.location is None:
        flags.append("no_coords")
    elif c.location.approx:
        flags.append("coords_approx")
    components = {
        "price": price,
        "route": route,
        "infrastructure": infra,
        "utilities": utilities,
        "settlement": settlement,
    }
    return components, flags


def _excluded_reason(
    c: _Candidate, suspicious: bool, primary: bool, settings: Settings, hidden: set[str]
) -> str | None:
    if c.location is None and not c.in_city:
        return "no_coords"
    if c.route is None:
        return "no_route"
    if c.route.km > settings.routing.max_route_km:
        return "too_far"
    if c.price_eur is None:
        return "no_price_eur"
    if c.price_eur > settings.scoring.max_price_eur:
        return "over_budget"
    if suspicious:
        return "suspicious_price"
    if not primary:
        return "duplicate"
    if c.row["id"] in hidden:
        return "hidden"
    return None
