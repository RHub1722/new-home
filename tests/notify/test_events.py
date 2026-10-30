from datetime import UTC, datetime, timedelta

import pytest

from homealize.config import Settings
from homealize.models import RunSummary
from homealize.notify.events import drop_share, plan_after_run
from homealize.storage import repo
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
SETTINGS = Settings()


def hours(n: float) -> datetime:
    return T0 + timedelta(hours=n)


def run(db, now, *, notified=True) -> int:
    run_id = repo.start_run(db, now)
    repo.finish_run(db, run_id, RunSummary(search_complete=True), now=now)
    if notified:
        repo.mark_notified(db, run_id, now=now)
    return run_id


def listing(db, lid, now, **overrides):
    repo.upsert_listing(
        db, make_listing(id=lid, url=f"https://999.md/ru/{lid}", **overrides),
        status="active", filter_reason=None, now=now,
    )


def keys(items):
    return [item.key for item in items]


def test_first_run_sends_only_a_summary(db):
    run_id = run(db, T0, notified=False)
    listing(db, "101", T0)
    listing(db, "102", T0)
    replace_scores(db, [score_record("101", rank=1, total=75.0), score_record("102", rank=2, total=65.0)])
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=T0)
    assert keys(items) == ["first_run"]
    assert "загружено 2 участка, в рейтинге 2" in items[0].text
    assert "/ad_101" in items[0].text and "/ad_102" in items[0].text


def test_new_listing_from_this_run_is_announced_with_its_photo(db):
    run(db, T0)
    listing(db, "101", T0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "102", hours(3))
    replace_scores(db, [score_record("101", rank=1, total=80.0), score_record("102", rank=2, total=75.0)])
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))
    assert keys(items) == ["new:102"]
    assert (items[0].kind, items[0].listing_id) == ("new", "102")
    assert items[0].preview_url == "https://i.simpalsmedia.com/999.md/BoardImages/900x900/aaa111.jpg"
    assert "Новый участок" in items[0].text


def test_new_listing_needs_a_high_score_or_a_top_place(db):
    run(db, T0)
    listing(db, "100", T0)
    run_id = run(db, hours(3), notified=False)
    for lid in ("101", "102", "103"):
        listing(db, lid, hours(3))
    replace_scores(db, [
        score_record("100", rank=1, total=90.0),
        score_record("101", rank=10, total=50.0),  # в топ-10
        score_record("102", rank=11, total=70.0),  # балл 70
        score_record("103", rank=12, total=69.0),  # ни то, ни другое
    ])
    assert keys(plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))) == ["new:101", "new:102"]


def test_excluded_and_reposted_listings_are_not_new(db):
    run(db, T0)
    listing(db, "100", T0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "101", hours(3))
    listing(db, "102", hours(3))
    replace_scores(db, [
        score_record("100", rank=None, excluded_reason="duplicate", duplicate_group="100"),
        score_record("101", rank=1, total=80.0, duplicate_group="100"),  # перевыставленная копия известного
        score_record("102", rank=None, total=90.0, excluded_reason="over_budget"),
    ])
    assert plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3)) == []


def test_more_than_ten_new_listings_end_with_one_summary_line(db):
    run(db, T0)
    listing(db, "100", T0)
    run_id = run(db, hours(3), notified=False)
    records = [score_record("100", rank=1, total=95.0)]
    for i in range(12):
        lid = str(200 + i)
        listing(db, lid, hours(3))
        records.append(score_record(lid, rank=i + 2, total=80.0))
    replace_scores(db, records)
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))
    assert len(items) == 11
    assert keys(items)[:2] == ["new:200", "new:201"]
    assert items[-1].key == f"new-more:{run_id}"
    assert "ещё 2 новых участка — /new" in items[-1].text


def test_listings_from_a_run_without_notifications_come_with_the_next_one(db):
    run(db, T0)
    listing(db, "100", T0)
    run(db, hours(3), notified=False)  # например, упала оценка: окно не закрыто
    listing(db, "101", hours(3))
    run_id = run(db, hours(6), notified=False)
    replace_scores(db, [score_record("100", rank=1, total=80.0), score_record("101", rank=2, total=75.0)])
    assert keys(plan_after_run(db, SETTINGS, run_id=run_id, now=hours(6))) == ["new:101"]


def test_window_never_reaches_back_more_than_a_day(db):
    run(db, T0)
    listing(db, "100", T0)
    run(db, hours(1), notified=False)
    listing(db, "101", hours(1))
    run_id = run(db, hours(30), notified=False)
    replace_scores(db, [score_record("100", rank=1, total=80.0), score_record("101", rank=2, total=75.0)])
    assert plan_after_run(db, SETTINGS, run_id=run_id, now=hours(30)) == []


def test_price_drop_of_five_percent_or_more_is_announced(db):
    run(db, T0)
    listing(db, "101", T0, price_original=30000.0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "101", hours(3), price_original=28000.0)
    replace_scores(db, [score_record("101", rank=1, total=65.0, price_eur=28000.0)])
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))
    history_id = repo.get_price_history(db, "101")[-1]["id"]
    assert keys(items) == [f"price_drop:101:{history_id}"]
    assert "было 30 000 €, стало 28 000 € (−7%)" in items[0].text


def test_small_drop_or_low_score_is_not_announced(db):
    run(db, T0)
    listing(db, "101", T0, price_original=30000.0)
    listing(db, "102", T0, price_original=30000.0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "101", hours(3), price_original=29000.0)  # −3%
    listing(db, "102", hours(3), price_original=27000.0)  # −10%, но балл 55
    replace_scores(db, [score_record("101", rank=1, total=80.0), score_record("102", rank=2, total=55.0)])
    assert plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3)) == []


def test_drop_across_currencies_is_compared_in_euro():
    rates = {"MDL": 1.0, "EUR": 20.0}
    eur = {"price_original": 30000.0, "currency": "EUR"}
    mdl = {"price_original": 500000.0, "currency": "MDL"}
    assert drop_share(eur, mdl, rates) == pytest.approx(1 - 25000 / 30000)
    assert drop_share(eur, mdl, None) is None
    assert drop_share(eur, {"price_original": 28500.0, "currency": "EUR"}, None) == pytest.approx(0.05)
