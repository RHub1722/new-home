from datetime import UTC, datetime

from homealize.storage import repo
from homealize.storage.scores import apply_hidden, assign_ranks, get_scored, load_scored, replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)


def seed(db, *records):
    for record in records:
        repo.upsert_listing(db, make_listing(id=record["listing_id"]), status="active", filter_reason=None, now=T0)
    replace_scores(db, list(records))


def ranks(db):
    return {row["listing_id"]: row["rank"] for row in db.execute("SELECT listing_id, rank FROM scores")}


def test_assign_ranks_orders_by_total_then_price_then_id(db):
    seed(
        db,
        score_record("c", total=60.0, rank=None),
        score_record("b", total=70.0, price_eur=30000.0, rank=None),
        score_record("a", total=70.0, price_eur=30000.0, rank=None),
        score_record("d", total=70.0, price_eur=25000.0, rank=None),
        score_record("x", total=90.0, excluded_reason="over_budget", rank=None),
    )
    assign_ranks(db)
    assert ranks(db) == {"d": 1, "a": 2, "b": 3, "c": 4, "x": None}


def test_apply_hidden_excludes_and_restores(db):
    seed(
        db,
        score_record("a", total=80.0),
        score_record("b", total=70.0),
        score_record("over", total=90.0, excluded_reason="over_budget", rank=None),
    )
    apply_hidden(db, {"a", "over"})
    rows = {row["listing_id"]: row for row in db.execute("SELECT * FROM scores")}
    assert (rows["a"]["excluded_reason"], rows["a"]["rank"]) == ("hidden", None)
    assert rows["b"]["rank"] == 1
    assert rows["over"]["excluded_reason"] == "over_budget"  # более конкретная причина остаётся
    apply_hidden(db, set())
    assert ranks(db) == {"a": 1, "b": 2, "over": None}


def test_get_scored_joins_listing_fields(db):
    seed(db, score_record("a"))
    row = get_scored(db, "a")
    assert (row["settlement"], row["url"]) == ("Гратиешты", "https://999.md/ru/100000001")
    assert "aaa111.jpg" in row["images"]
    assert get_scored(db, "missing") is None
    assert "images" in load_scored(db)[0].keys()


def test_scored_rows_carry_utilities_and_description(db):
    seed(db, score_record("a"))
    row = get_scored(db, "a")
    assert (row["has_electricity"], row["has_gas"], row["has_water"], row["has_sewerage"]) == (1, 1, 1, 0)
    assert row["description"] == "Ровный участок."
    assert row["site_updated_at"].startswith("2026-10-28")
    assert "settlement_id" in row.keys()
