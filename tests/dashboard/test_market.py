from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from homealize.dashboard.market import ALL_PLACES, Place, WeeklyMedian, market_table, week_start, weekly_medians
from homealize.storage import repo
from tests.factories import make_listing

ZONE = ZoneInfo("Europe/Chisinau")
MONDAY = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # понедельник, 11:00 по Кишинёву
THIS_WEEK, LAST_WEEK = date(2026, 11, 2), date(2026, 10, 26)
SINGERA = Place("Сынжера", "Новые Анены")
EVERYWHERE = Place(ALL_PLACES, None)


def add(db, listing_id, *, at, price=30000.0, settlement="Сынжера", region="Новые Анены", currency="EUR",
        filter_reason=None):
    listing = make_listing(
        id=listing_id, price_original=price, area_sot=6.0, settlement=settlement, region=region, currency=currency
    )
    status = "active" if filter_reason is None else "filtered_out"
    repo.upsert_listing(db, listing, status=status, filter_reason=filter_reason, now=at)
    db.commit()


def medians(db, *, rates=None, weeks=2, min_n=3):
    found = weekly_medians(db, zone=ZONE, rates=rates, now=MONDAY, weeks=weeks, min_n=min_n)
    return {(item.week, item.place): (item.n, item.median) for item in found}


def test_week_starts_on_local_monday():
    assert week_start(datetime(2026, 11, 1, 22, 30, tzinfo=UTC), ZONE) == THIS_WEEK  # 00:30 понедельника
    assert week_start(datetime(2026, 11, 1, 21, 30, tzinfo=UTC), ZONE) == LAST_WEEK  # 23:30 воскресенья


def test_median_per_settlement_and_for_all_places(db):
    for n, price in enumerate((30000.0, 36000.0, 42000.0)):
        add(db, f"s{n}", at=MONDAY, price=price)
    add(db, "v1", at=MONDAY, price=12000.0, settlement="Ватра", region="Кишинёв мун.")  # одно — село не показано
    assert medians(db, weeks=1) == {
        (THIS_WEEK, SINGERA): (3, 6000.0),
        (THIS_WEEK, EVERYWHERE): (4, 5500.0),
    }


def test_listing_counts_only_in_weeks_it_was_on_the_site(db):
    for n in range(3):
        add(db, f"old{n}", at=MONDAY - timedelta(weeks=1))
    result = medians(db)
    assert (LAST_WEEK, SINGERA) in result
    assert (THIS_WEEK, SINGERA) not in result


def test_week_uses_the_last_price_before_its_end(db):
    for n in range(3):
        add(db, f"s{n}", at=MONDAY - timedelta(weeks=1), price=30000.0)
        add(db, f"s{n}", at=MONDAY, price=24000.0)
    result = medians(db)
    assert result[(LAST_WEEK, SINGERA)] == (3, 5000.0)
    assert result[(THIS_WEEK, SINGERA)] == (3, 4000.0)


def test_other_currencies_need_a_rate(db):
    for n in range(3):
        add(db, f"m{n}", at=MONDAY, price=600000.0, currency="MDL")
    assert medians(db, weeks=1) == {}
    assert medians(db, weeks=1, rates={"MDL": 1.0, "EUR": 20.0})[(THIS_WEEK, SINGERA)] == (3, 5000.0)


def test_filtered_out_listings_are_ignored(db):
    for n in range(3):
        add(db, f"f{n}", at=MONDAY, filter_reason="area")
    assert medians(db, weeks=1) == {}


def test_market_table_orders_rows_and_compares_with_an_earlier_week():
    vatra = Place("Ватра", "Кишинёв мун.")
    weekly = [
        WeeklyMedian(date(2026, 10, 5), SINGERA, 4, 5000.0),
        WeeklyMedian(THIS_WEEK, vatra, 3, 7000.0),
        WeeklyMedian(THIS_WEEK, SINGERA, 5, 5500.0),
        WeeklyMedian(THIS_WEEK, EVERYWHERE, 8, 6000.0),
    ]
    rows = market_table(weekly, current=THIS_WEEK, compare_weeks=4)
    assert [(row.place.label, row.n, row.median, row.change_percent) for row in rows] == [
        ("Все сёла", 8, 6000.0, None),
        ("Сынжера (Новые Анены)", 5, 5500.0, 10.0),
        ("Ватра (Кишинёв мун.)", 3, 7000.0, None),
    ]
