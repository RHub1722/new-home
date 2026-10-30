from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.models import RunSummary
from homealize.notify import bot
from homealize.notify.bot import answer, handle_update
from homealize.notify.outbox import Notifier
from homealize.storage import marks, repo
from homealize.storage.scores import replace_scores
from tests.factories import FakeTelegram, make_listing, score_record

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
SETTINGS = Settings()


def seed(db):
    """Три участка в рейтинге (третий — новый за сутки) и один снятый с сайта."""
    for lid, first_seen in (
        ("100000001", NOW - timedelta(days=3)),
        ("100000002", NOW - timedelta(days=3)),
        ("100000003", NOW - timedelta(hours=2)),
        ("100000004", NOW - timedelta(days=3)),
    ):
        repo.upsert_listing(db, make_listing(id=lid, url=f"https://999.md/ru/{lid}"), status="active",
                            filter_reason=None, now=first_seen)
    db.execute("UPDATE listings SET status = 'removed' WHERE id = '100000004'")
    replace_scores(db, [
        score_record("100000001", rank=1, total=80.0),
        score_record("100000002", rank=2, total=75.0),
        score_record("100000003", rank=3, total=70.0),
    ])
    db.commit()


def reply(db, text, **kwargs):
    return answer(db, SETTINGS, text, now=NOW, **kwargs)


def message(text, chat=42, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": chat}, "text": text}}


def test_messages_from_other_chats_are_ignored(db):
    fake = FakeTelegram()
    handle_update(db, SETTINGS, Notifier(fake, 42), message("/top", chat=7), now=NOW)
    assert fake.sent == []


def test_reply_goes_to_the_own_chat(db):
    seed(db)
    fake = FakeTelegram()
    handle_update(db, SETTINGS, Notifier(fake, 42), message("/top"), now=NOW)
    assert fake.sent[0][0] == 42
    assert "<b>Топ-3</b>" in fake.sent[0][1]


def test_unknown_text_and_command_show_help(db):
    assert "/top [N]" in reply(db, "привет").text
    assert reply(db, "/foo").text.startswith("Не знаю команду /foo.")


def test_top_with_count_bot_name_and_limits(db):
    seed(db)
    assert reply(db, "/top 2").text.split("\n")[0] == "<b>Топ-2</b>"
    assert reply(db, "/top@HomealizeBot 1").text.split("\n")[1].startswith("1. 80 · Гратиешты")
    assert reply(db, "/top 0").text.split("\n")[0] == "<b>Топ-1</b>"
    assert "нужно число" in reply(db, "/top много").text


def test_top_when_the_rating_is_empty(db):
    assert "пусто" in reply(db, "/top").text


def test_ad_by_id_link_or_clickable_command(db):
    seed(db)
    for text in ("/ad 100000001", "/ad https://999.md/ru/100000001", "/ad_100000001"):
        result = reply(db, text)
        assert "Место в рейтинге: 1" in result.text
        assert result.preview_url == "https://i.simpalsmedia.com/999.md/BoardImages/900x900/aaa111.jpg"


def test_ad_for_unknown_and_unscored_listings(db):
    seed(db)
    assert reply(db, "/ad 123456789").text == "Объявления 123456789 нет в базе."
    assert "Укажите id" in reply(db, "/ad").text
    assert "не в оценке (снято с сайта)" in reply(db, "/ad 100000004").text


def test_hide_moves_the_listing_out_and_unhide_brings_it_back(db):
    seed(db)
    text = reply(db, "/hide 100000001").text
    assert text.startswith("Скрыто: Гратиешты, 6 сот. (/ad_100000001).")
    assert "/unhide_100000001" in text
    assert not db.in_transaction
    assert marks.marked(db, marks.HIDDEN) == {"100000001"}
    assert reply(db, "/top").text.split("\n")[1].startswith("1. 75")
    assert reply(db, "/hide 100000001").text.startswith("Уже скрыто")
    assert reply(db, "/unhide_100000001").text == (
        "Возвращено в рейтинг: Гратиешты, 6 сот. (/ad_100000001), место 1"
    )
    assert reply(db, "/unhide 100000001").text.startswith("Этот участок не был скрыт")


def test_favourites(db):
    seed(db)
    assert reply(db, "/fav 100000002").text.startswith("★ В избранном")
    assert reply(db, "/fav 100000002").text.startswith("Уже в избранном")
    assert "2. ★ 75" in reply(db, "/top").text
    assert reply(db, "/favs").text.split("\n")[0] == "<b>Избранное</b>: 1"
    assert reply(db, "/unfav 100000002").text.startswith("Убрано из избранного")
    assert "пока нет" in reply(db, "/favs").text


def test_new_lists_listings_of_the_last_day(db):
    seed(db)
    lines = reply(db, "/new").text.split("\n")
    assert lines[0] == "<b>Новые за сутки</b>: 1, в рейтинге 1"
    assert lines[1].startswith("3. 70")


def test_status_shows_the_last_run_with_short_errors(db):
    run_id = repo.start_run(db, datetime(2026, 10, 30, 5, 0, tzinfo=UTC))
    summary = RunSummary(search_complete=True, ads_seen=1780, new=3, errors=["x" * 300])
    repo.finish_run(db, run_id, summary, now=datetime(2026, 10, 30, 5, 1, 20, tzinfo=UTC))
    text = reply(db, "/status", next_run=datetime(2026, 10, 30, 8, 0, tzinfo=UTC)).text
    lines = text.split("\n")
    assert lines[0] == "<b>Последний прогон</b>: 30.10 07:00, ✅ успешен, 1 мин 20 с"
    assert lines[1].startswith("Увидено 1780, новых 3")
    assert lines[3] == "• " + "x" * 199 + "…"
    assert "Следующий прогон: 30.10 10:00" in text


def test_status_without_runs(db):
    assert reply(db, "/status").text == "Прогонов ещё не было."


def test_command_failure_is_answered_not_raised(db, monkeypatch):
    def broken(conn):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(bot, "load_scored", broken)
    fake = FakeTelegram()
    handle_update(db, SETTINGS, Notifier(fake, 42), message("/top"), now=NOW)
    assert fake.texts() == ["Не получилось выполнить команду — подробности в logs/homealize.log."]
