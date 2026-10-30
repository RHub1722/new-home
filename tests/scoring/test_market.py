from homealize.scoring.market import (
    DupItem,
    MarketItem,
    MarketMedians,
    Median,
    group_duplicates,
    is_suspicious,
)

LEVELS = {"min_settlement": 5, "min_route_band": 8, "band_km": 3}


def test_settlement_level_needs_five():
    items = [MarketItem(f"s{i}", 1, 10.0, value) for i, value in enumerate([4000, 5000, 6000, 7000, 8000])]
    medians = MarketMedians(items, settlement_names={1: "Сынжера"}, **LEVELS)
    assert medians.for_listing(1, 10.0) == Median(value=6000, level="settlement", n=5, label="село Сынжера")


def test_falls_back_to_route_band():
    near = [MarketItem(f"n{i}", 2, 10.0 + i * 0.3, 3000 + i * 100) for i in range(8)]
    far = [MarketItem(f"f{i}", 3, 25.0, 1000) for i in range(3)]
    medians = MarketMedians(near + far, settlement_names={}, **LEVELS)
    median = medians.for_listing(9, 11.0)
    assert (median.level, median.n, median.value) == ("route_band", 8, 3350)
    assert median.label == "участки в 8–14 км от города"


def test_falls_back_to_region():
    medians = MarketMedians([MarketItem("a", 1, 10.0, 1000), MarketItem("b", 2, 20.0, 3000)],
                            settlement_names={}, **LEVELS)
    median = medians.for_listing(5, None)
    assert (median.level, median.n, median.value, median.label) == ("region", 2, 2000, "весь регион")


def test_empty_market_gives_none():
    assert MarketMedians([], settlement_names={}, **LEVELS).for_listing(1, 5.0) is None


def test_is_suspicious():
    median = Median(4000, "settlement", 5, "x")
    assert is_suspicious(999.0, median, 0.25)
    assert not is_suspicious(1000.0, median, 0.25)
    assert not is_suspicious(None, median, 0.25)
    assert not is_suspicious(500.0, None, 0.25)


def test_group_duplicates():
    items = [
        DupItem("a", 6.0, 46.9300, 28.9400, 30000.0),
        DupItem("b", 6.0, 46.9302, 28.9401, 31000.0),
        DupItem("c", 6.0, 46.9400, 28.9400, 30000.0),
        DupItem("d", 7.0, 46.9300, 28.9400, 30000.0),
        DupItem("e", 6.0, 46.9301, 28.9400, 40000.0),
    ]
    assert group_duplicates(items, distance_m=50, price_tolerance=0.10) == {"a": ("a", True), "b": ("a", False)}


def test_cheapest_listing_is_primary():
    items = [DupItem("a", 6.0, 46.93, 28.94, 30000.0), DupItem("b", 6.0, 46.93, 28.94, 29000.0)]
    assert group_duplicates(items, distance_m=50, price_tolerance=0.10) == {"a": ("a", False), "b": ("a", True)}
