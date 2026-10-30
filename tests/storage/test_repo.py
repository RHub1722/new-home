import json
from datetime import UTC, datetime, timedelta

import pytest

from homealize.models import RunSummary
from homealize.storage import repo
from homealize.storage.repo import UpsertOutcome
from tests.factories import make_listing

T0 = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=3)


def save(db, listing, *, now=T0, status="active", reason=None):
    outcome = repo.upsert_listing(db, listing, status=status, filter_reason=reason, now=now)
    db.commit()
    return outcome


def test_new_listing_is_inserted_with_first_price(db):
    assert save(db, make_listing()) is UpsertOutcome.NEW
    row = repo.get_listing(db, "100000001")
    assert row["status"] == "active"
    assert row["first_seen_at"] == row["last_seen_at"] == T0.isoformat()
    assert row["missed_scans"] == 0
    assert row["price_eur"] == 28000.0
    assert row["price_per_sot_eur"] == 4666.67
    assert (row["has_gas"], row["has_sewerage"]) == (1, 0)
    assert json.loads(row["images"]) == list(make_listing().images)
    history = repo.get_price_history(db, "100000001")
    assert [(h["price_original"], h["currency"]) for h in history] == [(28000.0, "EUR")]


def test_same_listing_again_is_unchanged(db):
    save(db, make_listing())
    assert save(db, make_listing(), now=T1) is UpsertOutcome.UNCHANGED
    row = repo.get_listing(db, "100000001")
    assert row["first_seen_at"] == T0.isoformat()
    assert row["last_seen_at"] == T1.isoformat()
    assert len(repo.get_price_history(db, "100000001")) == 1


def test_price_change_is_recorded(db):
    save(db, make_listing())
    assert save(db, make_listing(price_original=26000.0), now=T1) is UpsertOutcome.PRICE_CHANGED
    assert [h["price_eur"] for h in repo.get_price_history(db, "100000001")] == [28000.0, 26000.0]
    assert repo.get_listing(db, "100000001")["price_eur"] == 26000.0


def test_other_change_is_update_without_price_history(db):
    save(db, make_listing())
    assert save(db, make_listing(has_gas=False), now=T1) is UpsertOutcome.UPDATED
    assert repo.get_listing(db, "100000001")["has_gas"] == 0
    assert len(repo.get_price_history(db, "100000001")) == 1


def test_price_disappearing_is_update_not_price_change(db):
    save(db, make_listing())
    assert save(db, make_listing(price_original=None, currency=None), now=T1) is UpsertOutcome.UPDATED
    assert len(repo.get_price_history(db, "100000001")) == 1


def test_filter_reason_is_saved(db):
    save(db, make_listing(region="Кагул"), status="filtered_out", reason="region")
    row = repo.get_listing(db, "100000001")
    assert (row["status"], row["filter_reason"]) == ("filtered_out", "region")


def test_filter_status_change_is_update(db):
    save(db, make_listing(region="Кагул"), status="filtered_out", reason="region")
    assert save(db, make_listing(), now=T1) is UpsertOutcome.UPDATED
    row = repo.get_listing(db, "100000001")
    assert (row["status"], row["filter_reason"]) == ("active", None)


def test_price_reappearing_unchanged_is_not_a_price_change(db):
    save(db, make_listing())
    assert save(db, make_listing(price_original=None, currency=None), now=T1) is UpsertOutcome.UPDATED
    assert save(db, make_listing(), now=T1 + timedelta(hours=3)) is UpsertOutcome.UPDATED
    assert len(repo.get_price_history(db, "100000001")) == 1


def test_price_reappearing_different_is_a_price_change(db):
    save(db, make_listing())
    save(db, make_listing(price_original=None, currency=None), now=T1)
    assert save(db, make_listing(price_original=25000.0), now=T1 + timedelta(hours=3)) is UpsertOutcome.PRICE_CHANGED
    assert [h["price_eur"] for h in repo.get_price_history(db, "100000001")] == [28000.0, 25000.0]


def test_unseen_listing_removed_after_threshold(db):
    save(db, make_listing(id="a"))
    save(db, make_listing(id="b"))
    assert repo.mark_unseen(db, {"a"}, threshold=2) == 0
    assert (repo.get_listing(db, "b")["missed_scans"], repo.get_listing(db, "b")["status"]) == (1, "active")
    assert repo.mark_unseen(db, {"a"}, threshold=2) == 1
    assert repo.get_listing(db, "b")["status"] == "removed"
    assert repo.get_listing(db, "a")["missed_scans"] == 0


def test_removed_listing_returns_to_active_when_seen_again(db):
    save(db, make_listing(id="b"))
    repo.mark_unseen(db, set(), threshold=1)
    assert repo.get_listing(db, "b")["status"] == "removed"
    assert save(db, make_listing(id="b"), now=T1) is UpsertOutcome.UPDATED
    row = repo.get_listing(db, "b")
    assert (row["status"], row["missed_scans"]) == ("active", 0)


def test_region_and_settlement_ids_are_saved(db):
    save(db, make_listing(region_id=12900, settlement_id=14010))
    row = repo.get_listing(db, "100000001")
    assert (row["region_id"], row["settlement_id"]) == (12900, 14010)


def test_count_not_removed_counts_active_and_filtered_out_only(db):
    assert repo.count_not_removed(db) == 0
    save(db, make_listing(id="a"))
    save(db, make_listing(id="b", region="Кагул"), status="filtered_out", reason="region")
    save(db, make_listing(id="c"))
    repo.mark_unseen(db, {"a", "b"}, threshold=1)
    assert repo.get_listing(db, "c")["status"] == "removed"
    assert repo.count_not_removed(db) == 2


def test_run_lifecycle(db):
    run_id = repo.start_run(db, T0)
    assert repo.last_run(db)["status"] == "running"
    summary = RunSummary(
        search_complete=True, ads_seen=5, new=2, changed=1, price_changed=1, removed=0,
        errors=["объявление x: 1 ошибок разбора"],
    )
    repo.finish_run(db, run_id, summary, now=T1)
    row = repo.last_run(db)
    assert row["status"] == "ok"
    assert row["finished_at"] == T1.isoformat()
    assert (row["ads_seen"], row["new"], row["changed"], row["price_changed"], row["removed"]) == (5, 2, 1, 1, 0)
    assert json.loads(row["errors"]) == ["объявление x: 1 ошибок разбора"]


def test_add_run_errors_appends_without_committing(db):
    run_id = repo.start_run(db, T0)
    repo.finish_run(db, run_id, RunSummary(search_complete=True, errors=["первая"]), now=T1)
    db.commit()
    repo.add_run_errors(db, run_id, ["вторая", "третья"])
    row = repo.last_run(db)
    assert json.loads(row["errors"]) == ["первая", "вторая", "третья"]
    assert "вторая" in row["errors"]  # кириллица хранится как есть, а не \uXXXX
    assert (row["status"], row["search_complete"]) == ("ok", 1)
    db.rollback()
    assert json.loads(repo.last_run(db)["errors"]) == ["первая"]


def test_add_run_errors_touches_only_the_given_run(db):
    first = repo.start_run(db, T0)
    second = repo.start_run(db, T1)
    repo.add_run_errors(db, first, ["x"])
    errors = {row["id"]: json.loads(row["errors"]) for row in db.execute("SELECT id, errors FROM runs")}
    assert errors == {first: ["x"], second: []}


def test_add_run_errors_without_messages_changes_nothing(db):
    run_id = repo.start_run(db, T0)
    db.commit()
    repo.add_run_errors(db, run_id, [])
    assert not db.in_transaction
    assert json.loads(repo.last_run(db)["errors"]) == []


def test_add_run_errors_for_unknown_run_is_an_error(db):
    with pytest.raises(ValueError, match="42"):
        repo.add_run_errors(db, 42, ["x"])


def test_close_stale_runs_marks_running_as_failed(db):
    stale = repo.start_run(db, T0)
    done = repo.start_run(db, T0)
    repo.finish_run(db, done, RunSummary(search_complete=True), now=T1)
    assert repo.close_stale_runs(db) == 1
    row = db.execute("SELECT * FROM runs WHERE id = ?", (stale,)).fetchone()
    assert row["status"] == "failed"
    assert row["finished_at"] is None
    assert json.loads(row["errors"]) == [repo.STALE_RUN_MESSAGE]
    assert db.execute("SELECT status FROM runs WHERE id = ?", (done,)).fetchone()[0] == "ok"
    assert repo.close_stale_runs(db) == 0


def test_notify_window_starts_after_the_last_notified_run(db):
    first = repo.start_run(db, T0)
    repo.mark_notified(db, first, now=T0)
    second = repo.start_run(db, T1)
    third = repo.start_run(db, T1 + timedelta(hours=3))
    day_ago = (T0 - timedelta(days=1)).isoformat()
    assert repo.notify_window_start(db, third, not_before=day_ago) == T1.isoformat()
    assert repo.notify_window_start(db, second, not_before=day_ago) == T1.isoformat()
    assert repo.notify_window_start(db, third, not_before=(T1 + timedelta(hours=1)).isoformat()) == (
        T1 + timedelta(hours=1)
    ).isoformat()
    assert db.execute("SELECT notified_at FROM runs WHERE id = ?", (first,)).fetchone()[0] == T0.isoformat()


def test_has_listings_before(db):
    save(db, make_listing(id="a"), now=T1)
    assert repo.has_listings_before(db, (T1 + timedelta(seconds=1)).isoformat())
    assert not repo.has_listings_before(db, T1.isoformat())


def test_mark_unseen_counts_only_active_listings(db):
    save(db, make_listing(id="a"))
    save(db, make_listing(id="f"), status="filtered_out", reason="region")
    assert repo.mark_unseen(db, set(), threshold=1) == 1
    assert repo.get_listing(db, "a")["status"] == "removed"
    assert repo.get_listing(db, "f")["status"] == "removed"
