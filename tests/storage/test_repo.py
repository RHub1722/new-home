import json
from datetime import UTC, datetime, timedelta

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
    save(db, make_listing(id="b"), now=T1)
    row = repo.get_listing(db, "b")
    assert (row["status"], row["missed_scans"]) == ("active", 0)


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
