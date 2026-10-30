from datetime import UTC, datetime, timedelta

from homealize.config import TelegramSettings
from homealize.notify import outbox
from homealize.notify.outbox import Notifier, Outgoing
from homealize.notify.telegram import TelegramError
from tests.factories import FakeTelegram

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
SETTINGS = TelegramSettings()


def two(db):
    outbox.enqueue(
        db,
        [
            Outgoing("new:a", "new", "A", listing_id="a", preview_url="https://p/1.jpg"),
            Outgoing("new:b", "new", "B", listing_id="b"),
        ],
        now=T0,
    )
    db.commit()


def statuses(db):
    return {row["key"]: row["status"] for row in db.execute("SELECT key, status FROM notifications")}


def test_enqueue_skips_known_keys(db):
    assert outbox.enqueue(db, [Outgoing("new:a", "new", "A", listing_id="a")], now=T0) == 1
    assert outbox.enqueue(db, [Outgoing("new:a", "new", "A ещё раз")], now=T0) == 0
    assert outbox.exists(db, "new:a") and not outbox.exists(db, "new:b")
    assert outbox.pending_count(db) == 1


def test_deliver_sends_in_order_marks_sent_and_pauses_between(db):
    two(db)
    fake, sleeps = FakeTelegram(), []
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=sleeps.append)
    assert (delivery.sent, delivery.failed, delivery.dropped) == (2, 0, 0)
    assert fake.sent == [(42, "A", "https://p/1.jpg"), (42, "B", None)]
    assert sleeps == [1.0]
    assert statuses(db) == {"new:a": "sent", "new:b": "sent"}
    assert not db.in_transaction
    assert outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=sleeps.append).sent == 0


def test_temporary_failure_stops_and_keeps_messages_pending(db):
    two(db)
    fake = FakeTelegram(errors=[TelegramError("нет сети")])
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert (delivery.sent, delivery.failed) == (0, 1)
    assert statuses(db) == {"new:a": "pending", "new:b": "pending"}
    row = db.execute("SELECT attempts, last_error FROM notifications WHERE key = 'new:a'").fetchone()
    assert (row["attempts"], row["last_error"]) == (1, "нет сети")
    assert outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None).sent == 2


def test_permanent_failure_drops_only_that_message(db):
    two(db)
    fake = FakeTelegram(errors=[TelegramError("Bad Request", permanent=True)])
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert (delivery.sent, delivery.dropped) == (1, 1)
    assert statuses(db) == {"new:a": "dropped", "new:b": "sent"}


def test_message_is_dropped_after_max_attempts(db):
    two(db)
    fake = FakeTelegram(errors=[TelegramError("нет сети")] * 3)
    for _ in range(3):
        outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert statuses(db)["new:a"] == "dropped"


def test_old_pending_messages_expire(db):
    two(db)
    fake = FakeTelegram()
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0 + timedelta(hours=25), sleep=lambda _s: None)
    assert (delivery.sent, delivery.dropped) == (0, 2)
    assert fake.sent == []


def test_flood_limit_waits_and_retries_once(db):
    outbox.enqueue(db, [Outgoing("alert:x:1", "alert", "тревога", topic="x")], now=T0)
    fake, sleeps = FakeTelegram(errors=[TelegramError("Too Many Requests", retry_after=3)]), []
    assert outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=sleeps.append).sent == 1
    assert sleeps == [3]


def test_last_created_by_kind_and_topic(db):
    assert outbox.last_created(db, "alert", "rate_limited") is None
    outbox.enqueue(db, [Outgoing("alert:rate_limited:1", "alert", "т", topic="rate_limited")], now=T0)
    assert outbox.last_created(db, "alert", "rate_limited") == T0
    assert outbox.last_created(db, "alert", "crashed") is None
