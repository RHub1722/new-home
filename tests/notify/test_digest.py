from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.notify import outbox
from homealize.notify.digest import plan_digest
from homealize.notify.outbox import Outgoing
from homealize.storage import marks, repo
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

EVENING = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)  # 20:00 по Кишинёву
OLD = EVENING - timedelta(days=3)
SETTINGS = Settings.model_validate({"telegram": {"digest_top": 3}})


def listing(db, lid, now=OLD):
    repo.upsert_listing(db, make_listing(id=lid, url=f"https://999.md/ru/{lid}"), status="active",
                        filter_reason=None, now=now)


def item(lid, rank, price=28000.0):
    return {"id": lid, "rank": rank, "price_eur": price, "settlement": "Гратиешты", "area_sot": 6.0}


def previous(db, top, *, day="2026-10-29"):
    outbox.enqueue(db, [Outgoing(f"digest:{day}", "digest", "вчера", topic=day, data={"top": top})],
                   now=EVENING - timedelta(days=1))


def test_first_digest_lists_the_top_and_counts_new(db):
    listing(db, "101")
    listing(db, "102", EVENING - timedelta(hours=5))
    listing(db, "103", EVENING - timedelta(hours=2))
    replace_scores(db, [
        score_record("101", rank=1, total=80.0),
        score_record("102", rank=2, total=75.0),
        score_record("103", rank=None, excluded_reason="over_budget"),
    ])
    digest = plan_digest(db, SETTINGS, now=EVENING)
    assert (digest.key, digest.kind, digest.topic) == ("digest:2026-10-30", "digest", "2026-10-30")
    lines = digest.text.split("\n")
    assert lines[0] == "📋 <b>Дайджест за 30.10</b>"
    assert lines[1] == "Новых за сутки: 2, из них в рейтинге 1."
    assert lines[3] == "Топ-2:"
    assert lines[4] == "1. 80 · Гратиешты, 6 сот., 28 000 € (−17%), 10 мин /ad_101"
    assert "Выпали" not in digest.text
    assert digest.data["top"] == [item("101", 1), item("102", 2)]


def test_digest_shows_moves_against_the_previous_one(db):
    for lid in ("101", "102", "103", "104"):
        listing(db, lid)
    previous(db, [item("102", 1), item("101", 2), item("103", 3)])
    replace_scores(db, [
        score_record("101", rank=1, total=80.0),
        score_record("102", rank=2, total=75.0),
        score_record("104", rank=3, total=72.0),
        score_record("103", rank=4, total=60.0),
    ])
    text = plan_digest(db, SETTINGS, now=EVENING).text
    assert "1. 80 · Гратиешты, 6 сот., 28 000 € (−17%), 10 мин · ↑1 /ad_101" in text
    assert "· ↓1 /ad_102" in text
    assert "· 🆕 в топе /ad_104" in text
    assert "• Гратиешты, 6 сот. (было 3-е место) — опустился на 4-е место /ad_103" in text


def test_dropped_listings_explain_why(db):
    for lid in ("101", "102", "103", "105", "106", "107"):
        listing(db, lid)
    db.execute("UPDATE listings SET status = 'removed' WHERE id = '101'")
    marks.add_mark(db, "102", marks.HIDDEN, now=EVENING)
    previous(db, [item("101", 1), item("102", 2), item("103", 3)])
    replace_scores(db, [
        score_record("105", rank=1, total=85.0),
        score_record("106", rank=2, total=84.0),
        score_record("107", rank=3, total=83.0),
        score_record("102", rank=None, excluded_reason="hidden"),
        score_record("103", rank=None, excluded_reason="over_budget", price_eur=52000.0),
    ])
    text = plan_digest(db, SETTINGS, now=EVENING).text
    assert "— снято с сайта /ad_101" in text
    assert "— скрыто вами /ad_102" in text
    assert "— подорожал: 28 000 € → 52 000 € (дороже бюджета) /ad_103" in text


def test_digest_day_is_the_local_date(db):
    late = datetime(2026, 10, 30, 22, 30, tzinfo=UTC)  # 00:30 31 октября по Кишинёву
    assert plan_digest(db, SETTINGS, now=late).key == "digest:2026-10-31"


def test_empty_rating(db):
    assert "В рейтинге пока пусто." in plan_digest(db, SETTINGS, now=EVENING).text


def test_todays_digest_is_not_compared_with_itself(db):
    listing(db, "101")
    replace_scores(db, [score_record("101", rank=1, total=80.0)])
    previous(db, [item("999", 1)], day="2026-10-30")
    text = plan_digest(db, SETTINGS, now=EVENING).text
    assert "Выпали" not in text and "↑" not in text
