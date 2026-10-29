import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from homealize.config import Settings
from homealize.pipeline import run_once
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage import repo
from tests.factories import FakeSource, load_fixture, make_listing, page

T0 = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
SETTINGS = Settings()


def at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def test_first_run_saves_active_and_filtered(db):
    source = FakeSource([page(make_listing(id="a"), make_listing(id="b", region="Кагул"))])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert (summary.search_complete, summary.ads_seen, summary.new) == (True, 2, 2)
    assert repo.get_listing(db, "a")["status"] == "active"
    b = repo.get_listing(db, "b")
    assert (b["status"], b["filter_reason"]) == ("filtered_out", "region")
    assert repo.last_run(db)["status"] == "ok"


def test_price_drop_detected_on_next_run(db):
    run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=T0)
    summary = run_once(db, FakeSource([page(make_listing(id="a", price_original=25000.0))]), SETTINGS, now=at(3))
    assert (summary.new, summary.price_changed) == (0, 1)
    assert [h["price_eur"] for h in repo.get_price_history(db, "a")] == [28000.0, 25000.0]


def test_listing_removed_after_two_complete_runs_without_it(db):
    run_once(db, FakeSource([page(make_listing(id="a"), make_listing(id="b"))]), SETTINGS, now=T0)
    second = run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=at(3))
    assert second.removed == 0
    third = run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=at(6))
    assert third.removed == 1
    assert repo.get_listing(db, "b")["status"] == "removed"


def test_interrupted_scan_keeps_saved_pages_and_removes_nothing(db):
    run_once(db, FakeSource([page(make_listing(id="a"), make_listing(id="b"))]), SETTINGS, now=T0)
    broken = FakeSource([page(make_listing(id="a")), page(make_listing(id="c"))], fail_at=1)
    summary = run_once(db, broken, SETTINGS, now=at(3))
    assert summary.search_complete is False
    assert any("SourceUnavailableError" in error for error in summary.errors)
    assert repo.get_listing(db, "a")["last_seen_at"] == at(3).isoformat()
    assert repo.get_listing(db, "b")["missed_scans"] == 0
    assert repo.get_listing(db, "c") is None
    assert repo.last_run(db)["status"] == "failed"


def test_duplicates_across_pages_counted_once(db):
    source = FakeSource([
        page(make_listing(id="a"), make_listing(id="b")),
        page(make_listing(id="b"), make_listing(id="c")),
    ])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert (summary.ads_seen, summary.new) == (3, 3)


def test_parse_errors_are_saved_in_run(db):
    source = FakeSource([page(*parsable(5), errors=("объявление x: 2 ошибок разбора",), unparsed=("x",))])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert summary.search_complete is True
    assert summary.errors == ["объявление x: 2 ошибок разбора"]
    assert json.loads(repo.last_run(db)["errors"]) == summary.errors


def test_unexpected_error_marks_run_failed_and_propagates(db):
    source = FakeSource([page(make_listing(id="a"))], fail_at=0, error=RuntimeError("баг"))
    with pytest.raises(RuntimeError):
        run_once(db, source, SETTINGS, now=T0)
    run = repo.last_run(db)
    assert run["status"] == "failed"
    assert json.loads(run["errors"]) == ["RuntimeError: баг"]


def test_removal_failure_rolls_back_and_marks_run_failed(db, monkeypatch):
    run_once(db, FakeSource([page(make_listing(id="a"), make_listing(id="b"))]), SETTINGS, now=T0)
    real_mark_unseen = repo.mark_unseen

    def half_done_then_fail(conn, seen_ids, *, threshold):
        real_mark_unseen(conn, seen_ids, threshold=threshold)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "mark_unseen", half_done_then_fail)
    with pytest.raises(sqlite3.OperationalError):
        run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=at(3))
    run = repo.last_run(db)
    assert run["status"] == "failed"
    assert json.loads(run["errors"]) == ["OperationalError: database is locked"]
    assert repo.get_listing(db, "b")["missed_scans"] == 0
    assert not db.in_transaction


def test_end_to_end_with_999_fixtures_over_http(db):
    pages_by_skip = {0: "search_page_1.json", 4: "search_page_2.json"}

    def handler(request: httpx.Request) -> httpx.Response:
        skip = json.loads(request.content)["variables"]["input"]["pagination"]["skip"]
        return httpx.Response(200, json=load_fixture(pages_by_skip[skip]))

    settings = Settings.model_validate({"source": {"page_size": 4, "delay_seconds": [0, 0]}})
    source = build_nine99_source(settings, transport=httpx.MockTransport(handler))
    try:
        summary = run_once(db, source, settings, now=T0)
    finally:
        source.close()
    assert summary.search_complete
    assert (summary.ads_seen, summary.new) == (5, 5)
    assert len(summary.errors) == 1
    statuses = {
        row["id"]: (row["status"], row["filter_reason"])
        for row in db.execute("SELECT id, status, filter_reason FROM listings")
    }
    assert statuses == {
        "100000001": ("active", None),
        "100000002": ("active", None),
        "100000003": ("filtered_out", "region"),
        "100000004": ("filtered_out", "area"),
        "100000006": ("filtered_out", "no_price"),
    }
    assert "[телефон удалён]" in repo.get_listing(db, "100000001")["description"]


def parsable(count: int, start: int = 0) -> list:
    return [make_listing(id=f"p{n}") for n in range(start, start + count)]


def test_unparsable_ad_is_not_counted_as_missed(db):
    run_once(db, FakeSource([page(make_listing(id="b"), *parsable(5))]), SETTINGS, now=T0)
    drifted = page(*parsable(5), errors=("объявление b: region.value — ошибка (ошибок: 1)",), unparsed=("b",))
    for hours in (3, 6):
        summary = run_once(db, FakeSource([drifted]), SETTINGS, now=at(hours))
        assert summary.search_complete is True
        assert summary.removed == 0
    row = repo.get_listing(db, "b")
    assert (row["status"], row["missed_scans"]) == ("active", 0)


def test_unparsable_ad_is_not_saved_or_counted_as_seen(db):
    source = FakeSource([page(make_listing(id="a"), errors=("объявление b: x",), unparsed=("b",))])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert (summary.ads_seen, summary.new) == (1, 1)
    assert repo.get_listing(db, "b") is None


def test_ad_unparsable_on_one_page_but_parsable_on_another_is_still_saved(db):
    source = FakeSource([
        page(make_listing(id="a"), errors=("объявление b: x",), unparsed=("b",)),
        page(make_listing(id="b")),
    ])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert (summary.ads_seen, summary.new) == (2, 2)
    assert repo.get_listing(db, "b")["status"] == "active"


def test_too_many_unparsable_ads_skip_removal_and_fail_the_run(db):
    run_once(db, FakeSource([page(make_listing(id="a"), make_listing(id="b"), make_listing(id="c"))]), SETTINGS, now=T0)
    broken = page(make_listing(id="a"), errors=("объявление x: 1",), unparsed=("x",))
    summary = run_once(db, FakeSource([broken]), SETTINGS, now=at(3))
    assert summary.search_complete is False
    assert summary.removed == 0
    assert "Слишком много нераспознанных объявлений: 1 из 2 — снятие пропущено" in summary.errors
    assert repo.last_run(db)["status"] == "failed"
    assert repo.get_listing(db, "b")["missed_scans"] == 0
    assert repo.get_listing(db, "a")["last_seen_at"] == at(3).isoformat()


def test_unparsable_share_exactly_at_limit_is_allowed(db):
    source = FakeSource([page(*parsable(4), errors=("объявление x: 1",), unparsed=("x",))])
    assert run_once(db, source, SETTINGS, now=T0).search_complete is True


def test_unparsable_share_counts_across_pages(db):
    source = FakeSource([
        page(*parsable(3), errors=("объявление x: 1",), unparsed=("x",)),
        page(*parsable(1, start=3), errors=("объявление y: 1",), unparsed=("y",)),
    ])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert summary.search_complete is False
    assert "Слишком много нераспознанных объявлений: 2 из 6 — снятие пропущено" in summary.errors


def test_miss_seen_miss_keeps_listing_active(db):
    both = [make_listing(id="a"), make_listing(id="b")]
    run_once(db, FakeSource([page(*both)]), SETTINGS, now=T0)
    run_once(db, FakeSource([page(both[0])]), SETTINGS, now=at(3))
    run_once(db, FakeSource([page(*both)]), SETTINGS, now=at(6))
    run_once(db, FakeSource([page(both[0])]), SETTINGS, now=at(9))
    row = repo.get_listing(db, "b")
    assert (row["status"], row["missed_scans"]) == ("active", 1)
