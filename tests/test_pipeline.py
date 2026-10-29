import json
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
    source = FakeSource([page(make_listing(id="a"), errors=("объявление x: 2 ошибок разбора",))])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert summary.errors == ["объявление x: 2 ошибок разбора"]
    assert json.loads(repo.last_run(db)["errors"]) == summary.errors


def test_unexpected_error_marks_run_failed_and_propagates(db):
    source = FakeSource([page(make_listing(id="a"))], fail_at=0, error=RuntimeError("баг"))
    with pytest.raises(RuntimeError):
        run_once(db, source, SETTINGS, now=T0)
    run = repo.last_run(db)
    assert run["status"] == "failed"
    assert json.loads(run["errors"]) == ["RuntimeError: баг"]


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
