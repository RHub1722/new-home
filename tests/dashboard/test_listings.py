from datetime import UTC, datetime

from homealize.dashboard.listings import (
    PRICE_STEP,
    Filters,
    apply_filters,
    bounds,
    candidates,
    labels,
    load_listings,
    picked_listing_id,
    score_color,
    settlement_options,
)
from homealize.storage import marks, repo
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def seed(db, *items, favs=(), hidden=()):
    """items — пары (поля объявления, поля оценки); id берётся из полей объявления."""
    records = []
    for listing, score in items:
        repo.upsert_listing(db, make_listing(**listing), status="active", filter_reason=None, now=T0)
        records.append(score_record(listing["id"], **score))
    replace_scores(db, records)
    for listing_id in favs:
        marks.add_mark(db, listing_id, marks.FAV, now=T0)
    for listing_id in hidden:
        marks.add_mark(db, listing_id, marks.HIDDEN, now=T0)
    db.commit()


def ids(rows):
    return [row.listing_id for row in rows]


def test_load_listings_builds_views(db):
    seed(
        db,
        ({"id": "a"}, {"total": 80.0, "rank": 1}),
        ({"id": "b", "settlement": None}, {"total": 90.0, "rank": None, "excluded_reason": "over_budget"}),
        favs=["a"],
    )
    a, b = load_listings(db)
    assert (a.listing_id, a.rank, a.total, a.fav, a.hidden) == ("a", 1, 80.0, True, False)
    assert (a.settlement, a.region, a.area_sot) == ("Гратиешты", "Кишинёв мун.", 6.0)
    assert a.deviation_percent == -17  # 4666,67 €/сот. при медиане 5600
    assert a.utilities == frozenset({"electricity", "gas", "water"})
    assert a.flags == ("no_sewerage",)
    assert (a.route_min, a.lat, a.lon, a.group_extra) == (10.0, 47.09921, 28.8165, 0)
    assert a.url == "https://999.md/ru/100000001"
    assert (b.settlement, b.excluded_reason, b.fav) == ("село не указано", "over_budget", False)


def test_group_extra_counts_other_members_of_the_group(db):
    duplicate = {"duplicate_group": "a", "is_group_primary": False, "rank": None, "excluded_reason": "duplicate"}
    seed(
        db,
        ({"id": "a"}, {"duplicate_group": "a", "rank": 1}),
        ({"id": "b"}, duplicate),
        ({"id": "c"}, duplicate),
        ({"id": "d"}, {"rank": 2}),
    )
    views = {row.listing_id: row for row in load_listings(db)}
    assert (views["a"].group_extra, views["d"].group_extra) == (2, 0)
    assert labels(views["a"]) == "ещё 2 объявления этого участка; ⚠ нет канализации (не указано в объявлении)"


def test_labels_name_the_exclusion_reason_first(db):
    seed(db, ({"id": "a"}, {"rank": None, "excluded_reason": "over_budget", "flags": '["suspicious_price"]'}))
    (row,) = load_listings(db)
    assert labels(row) == "дороже бюджета; 🔍 подозрительно дёшево — проверить вручную"


def test_default_filters_show_only_the_ranking(db):
    seed(
        db,
        ({"id": "a"}, {"total": 80.0, "rank": 1}),
        ({"id": "b"}, {"total": 95.0, "rank": None, "excluded_reason": "over_budget"}),
        ({"id": "c"}, {"total": 60.0, "rank": None, "excluded_reason": "suspicious_price"}),
        ({"id": "d"}, {"total": 75.0, "rank": None, "excluded_reason": "hidden"}),
        ({"id": "e"}, {"total": 99.0, "rank": None, "excluded_reason": "too_far"}),
        hidden=["d"],
    )
    rows = load_listings(db)
    assert ids(apply_filters(rows, Filters())) == ["a"]
    everything = Filters(show_hidden=True, show_suspicious=True, show_over_budget=True)
    assert ids(apply_filters(rows, everything)) == ["a", "b", "d", "c"]  # сначала места, потом по баллу
    assert ids(candidates(rows)) == ["a", "b", "d", "c"]  # «слишком далеко» дашборд не показывает


def test_hidden_listing_with_another_reason_needs_both_switches(db):
    seed(db, ({"id": "a"}, {"rank": None, "excluded_reason": "over_budget"}), hidden=["a"])
    rows = load_listings(db)
    assert apply_filters(rows, Filters(show_over_budget=True)) == []
    assert ids(apply_filters(rows, Filters(show_over_budget=True, show_hidden=True))) == ["a"]


def test_numeric_and_set_filters(db):
    seed(
        db,
        ({"id": "a", "area_sot": 6.0, "settlement": "Сынжера"}, {"rank": 1, "price_eur": 28000.0, "route_min": 10.0}),
        (
            {"id": "b", "area_sot": 8.0, "settlement": "Ватра", "has_sewerage": True},
            {"rank": 2, "price_eur": 40000.0, "route_min": 20.0},
        ),
        favs=["b"],
    )
    rows = load_listings(db)
    assert ids(apply_filters(rows, Filters(price=(28000, 30000)))) == ["a"]  # границы включительно
    assert ids(apply_filters(rows, Filters(area=(7.0, 10.0)))) == ["b"]
    assert ids(apply_filters(rows, Filters(max_minutes=10.0))) == ["a"]
    assert ids(apply_filters(rows, Filters(utilities=frozenset({"sewerage"})))) == ["b"]
    assert ids(apply_filters(rows, Filters(settlements=frozenset({"Сынжера"})))) == ["a"]
    assert ids(apply_filters(rows, Filters(only_favs=True))) == ["b"]


def seed_for_bounds(db):
    seed(
        db,
        ({"id": "a", "area_sot": 5.3, "settlement": "Сынжера"}, {"rank": 1, "price_eur": 28100.0, "route_min": 9.2}),
        ({"id": "b", "area_sot": 9.6, "settlement": "Ватра"}, {"rank": 2, "price_eur": 40200.0, "route_min": 12.0}),
        (
            {"id": "c", "area_sot": 20.0, "settlement": "Бачой"},
            {"rank": None, "excluded_reason": "over_budget", "price_eur": 61900.0, "route_min": 21.5},
        ),
        (
            {"id": "d", "area_sot": 5.0, "settlement": "Ватра"},
            {"rank": None, "excluded_reason": "suspicious_price", "price_eur": 0.1, "route_min": 3.0},
        ),
        (
            {"id": "e", "area_sot": 6.0, "settlement": "Ватра"},
            {"rank": None, "excluded_reason": "hidden", "price_eur": 33000.0, "route_min": 40.0},
        ),
        (
            {"id": "f", "area_sot": 7.0, "settlement": "Ватра"},
            {"rank": None, "excluded_reason": "over_budget", "price_eur": 99000.0, "route_min": 10.0},
        ),
        ({"id": "g", "settlement": "Ятрань"}, {"rank": None, "excluded_reason": "too_far", "price_eur": 1000.0}),
        hidden=["e", "f"],
    )
    return load_listings(db)


def test_bounds_follow_the_rows_the_switches_can_show(db):
    rows = seed_for_bounds(db)
    default = bounds(rows)  # только рейтинг: ни 0,1 €, ни 99 000 € ползунок не растягивают
    assert default.price == (28000.0, 40500.0)
    assert default.area == (5.0, 10.0)
    assert default.max_minutes == 12.0
    assert bounds(rows, show_over_budget=True).price == (28000.0, 62000.0)  # скрытое «дороже бюджета» (f) не в счёт
    assert bounds(rows, show_suspicious=True).price == (0.0, 40500.0)
    assert bounds(rows, show_hidden=True).max_minutes == 40.0
    assert bounds(rows, show_hidden=True).price == (28000.0, 40500.0)
    assert bounds(rows, show_hidden=True, show_over_budget=True).price == (28000.0, 99000.0)  # теперь и f
    assert bounds(rows, show_over_budget=True).area == (5.0, 20.0)
    assert bounds(rows, show_over_budget=True).max_minutes == 22.0


def test_bounds_price_is_snapped_to_the_slider_step(db):
    rows = seed_for_bounds(db)
    assert PRICE_STEP == 500.0
    for limits in (bounds(rows), bounds(rows, show_over_budget=True), bounds(rows, show_suspicious=True)):
        assert all(value % PRICE_STEP == 0 for value in limits.price)
    assert bounds(rows, show_over_budget=True).price == (28000.0, 62000.0)  # 28 100 вниз, 61 900 вверх


def test_bounds_is_none_when_nothing_can_be_shown(db):
    assert bounds([]) is None
    seed(db, ({"id": "a"}, {"rank": None, "excluded_reason": "over_budget"}))
    rows = load_listings(db)
    assert bounds(rows) is None  # в рейтинге пусто, а «дороже бюджета» выключено
    assert bounds(rows, show_over_budget=True) is not None


def test_settlement_options_cover_every_candidate(db):
    rows = seed_for_bounds(db)
    # переключатели ни при чём: село можно выбрать, даже если его строки сейчас скрыты; «слишком далеко» не в счёт
    assert settlement_options(rows) == ["Бачой", "Ватра", "Сынжера"]
    assert settlement_options([]) == []


def test_score_color_runs_red_yellow_green():
    assert score_color(0) == (0xF8, 0x69, 0x6B)
    assert score_color(50) == (0xFF, 0xEB, 0x84)
    assert score_color(100) == (0x63, 0xBE, 0x7B)
    assert score_color(25) == (252, 170, 120)
    assert score_color(-5) == score_color(0)
    assert score_color(150) == score_color(100)


def test_picked_listing_id_reads_the_map_selection():
    selection = {"indices": {"listings": [0]}, "objects": {"listings": [{"id": "a", "lat": 47.0}]}}
    assert picked_listing_id(selection) == "a"
    assert picked_listing_id({"indices": {}, "objects": {"entries": [{"title": "Въезд"}]}}) is None
    assert picked_listing_id({"indices": {}, "objects": {}}) is None
    assert picked_listing_id(None) is None
