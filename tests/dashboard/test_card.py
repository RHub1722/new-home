from datetime import UTC, datetime, timedelta

import pytest

from homealize.config import Settings
from homealize.dashboard.card import MarkBusyError, load_card, nearby_pois, set_mark
from homealize.models import Poi
from homealize.runlock import hold_lock
from homealize.storage import marks, poi_cache, repo
from homealize.storage.db import connect, migrate
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
LAT, LON = 47.09921, 28.8165  # точка make_listing


@pytest.fixture
def settings(tmp_path):
    return Settings.model_validate({"storage": {"run_lock_path": str(tmp_path / "run.lock")}})


def seed(conn, *records, images=None):
    extra = {"images": images} if images is not None else {}
    for record in records:
        repo.upsert_listing(
            conn, make_listing(id=record["listing_id"], **extra), status="active", filter_reason=None, now=T0
        )
    replace_scores(conn, list(records))
    conn.commit()


def test_card_holds_breakdown_marks_and_photos(db, settings):
    photos = tuple(f"https://i.simpalsmedia.com/999.md/BoardImages/900x900/{n}.jpg" for n in range(8))
    seed(db, score_record("a"), images=photos)
    marks.add_mark(db, "a", marks.FAV, now=T0)
    card = load_card(db, settings, "a")
    assert (card.fav, card.hidden) == (True, False)
    assert card.photos == list(photos[:6])
    assert card.row["settlement"] == "Гратиешты"
    assert [line.name for line in card.components] == ["price", "route", "infrastructure", "utilities", "settlement"]
    price = card.components[0]
    assert (price.label, price.score, price.weight, price.points) == ("Цена", 66.7, 0.30, 20.0)
    assert price.note == "Цена 67: на 17% дешевле медианы"
    assert card.components[2].note == ""  # пояснения к инфраструктуре в этой оценке нет


def test_card_is_none_for_listing_outside_the_scores(db, settings):
    assert load_card(db, settings, "missing") is None


def test_card_history_follows_price_changes(db, settings):
    seed(db, score_record("a"))
    later = T0 + timedelta(days=1)
    repo.upsert_listing(db, make_listing(id="a", price_original=26000.0), status="active", filter_reason=None, now=later)
    db.commit()
    history = load_card(db, settings, "a").history
    assert [(point.price, point.currency) for point in history] == [(28000.0, "EUR"), (26000.0, "EUR")]
    assert history[1].observed_at == later


def test_nearby_pois_name_the_nearest_object_of_each_category(db):
    poi_cache.replace_poi(
        db,
        [
            Poi(key="node/1", category="supermarket", name="Linella", brand=None, lat=LAT + 0.0009, lon=LON),  # ≈100 м
            Poi(key="node/2", category="supermarket", name="Fidesco", brand=None, lat=LAT + 0.0027, lon=LON),  # ≈300 м
            Poi(key="node/3", category="pharmacy", name=None, brand="Felicia", lat=LAT, lon=LON + 0.004),  # ≈300 м
            Poi(key="node/4", category="school", name="Лицей", brand=None, lat=LAT + 0.02, lon=LON),  # ≈2,2 км
        ],
        now=T0,
    )
    nearby = nearby_pois(db, LAT, LON, max_m=1500)
    assert [(poi.category, poi.label, poi.name) for poi in nearby] == [
        ("supermarket", "супермаркет", "Linella"),
        ("pharmacy", "аптека", "Felicia"),
    ]
    assert 95 <= nearby[0].distance_m <= 105
    assert nearby_pois(db, None, None, max_m=1500) == []


def test_set_mark_hides_and_restores_with_new_ranks(db, settings):
    seed(db, score_record("a", total=80.0, rank=1), score_record("b", total=70.0, rank=2))
    assert set_mark(db, settings, "a", marks.HIDDEN, on=True, now=T0) is True
    assert set_mark(db, settings, "a", marks.HIDDEN, on=True, now=T0) is False
    rows = {row["listing_id"]: row for row in db.execute("SELECT * FROM scores")}
    assert (rows["a"]["excluded_reason"], rows["a"]["rank"], rows["b"]["rank"]) == ("hidden", None, 1)
    assert set_mark(db, settings, "a", marks.HIDDEN, on=False, now=T0) is True
    assert {row["listing_id"]: row["rank"] for row in db.execute("SELECT * FROM scores")} == {"a": 1, "b": 2}
    assert load_card(db, settings, "a").hidden is False


def test_set_mark_commits_the_fav(tmp_path, settings):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    seed(conn, score_record("a"))
    assert set_mark(conn, settings, "a", marks.FAV, on=True, now=T0) is True
    other = connect(tmp_path / "test.db")
    try:
        assert marks.marked(other, marks.FAV) == {"a"}
    finally:
        other.close()
        conn.close()


def test_set_mark_refuses_while_a_run_holds_the_lock(db, settings):
    seed(db, score_record("a"))
    with hold_lock(settings.storage.run_lock_path), pytest.raises(MarkBusyError):
        set_mark(db, settings, "a", marks.FAV, on=True, now=T0)
    assert marks.marked(db, marks.FAV) == set()


def test_set_mark_rejects_unknown_marks(db, settings):
    seed(db, score_record("a"))
    with pytest.raises(ValueError):
        set_mark(db, settings, "a", "pinned", on=True, now=T0)
