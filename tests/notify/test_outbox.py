import json
from datetime import UTC, datetime, timedelta

import httpx

from homealize.config import TelegramSettings
from homealize.notify import outbox
from homealize.notify.outbox import Notifier, Outgoing
from homealize.notify.telegram import TelegramClient, TelegramError, split_text
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


LONG_TEXT = "\n".join(f"строка {i} " + "x" * 90 for i in range(100))  # три части по Telegram-лимиту
LONG_PARTS = split_text(LONG_TEXT)


def long_message(db):
    outbox.enqueue(
        db, [Outgoing("digest:1", "digest", LONG_TEXT, topic="2026-10-30", preview_url="https://p/1.jpg")], now=T0
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


def test_long_message_resumes_after_the_last_delivered_part(db):
    assert len(LONG_PARTS) == 3
    long_message(db)
    fake = FakeTelegram(errors=[None, TelegramError("нет сети")])  # часть 1 уходит, часть 2 — сбой
    first = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert (first.sent, first.failed) == (0, 1)
    assert fake.sent == [(42, LONG_PARTS[0], "https://p/1.jpg")]
    row = db.execute("SELECT status, attempts, payload FROM notifications").fetchone()
    assert (row["status"], row["attempts"]) == ("pending", 1)
    assert json.loads(row["payload"])["sent_parts"] == 1
    assert not db.in_transaction

    second = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert second.sent == 1
    assert fake.texts() == LONG_PARTS  # каждая часть ровно один раз
    assert [preview for _chat, _text, preview in fake.sent] == ["https://p/1.jpg", None, None]
    assert statuses(db) == {"digest:1": "sent"}
    assert outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None).sent == 0
    assert fake.texts() == LONG_PARTS


def test_flood_limit_on_a_later_part_does_not_resend_the_earlier_ones(db):
    long_message(db)
    fake, sleeps = FakeTelegram(errors=[None, TelegramError("Too Many Requests", retry_after=3)]), []
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=sleeps.append)
    assert (delivery.sent, delivery.failed) == (1, 0)
    assert sleeps == [3]
    assert fake.texts() == LONG_PARTS  # часть 1 — ровно один раз, ждали и повторили только часть 2
    assert [preview for _chat, _text, preview in fake.sent] == ["https://p/1.jpg", None, None]
    assert statuses(db) == {"digest:1": "sent"}


def test_one_part_message_keeps_its_payload(db):
    outbox.enqueue(db, [Outgoing("new:a", "new", "A", listing_id="a", data={"top": [1, 2]})], now=T0)
    before = db.execute("SELECT payload FROM notifications").fetchone()["payload"]
    outbox.deliver(db, Notifier(FakeTelegram(), 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert db.execute("SELECT payload FROM notifications").fetchone()["payload"] == before


def test_real_client_sends_every_part_of_a_long_message_once_despite_a_flood_limit(db):
    long_message(db)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content)["text"])
        if len(requests) == 2:  # на второй части Telegram один раз просит подождать
            return httpx.Response(
                429,
                json={"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 2}},
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

    client = TelegramClient(
        "123456:SECRET-token", api_url="https://api.telegram.org", timeout_seconds=5, transport=httpx.MockTransport(handler)
    )
    sleeps = []
    delivery = outbox.deliver(db, Notifier(client, 42), SETTINGS, now=T0, sleep=sleeps.append)
    assert (delivery.sent, sleeps) == (1, [2.0])
    assert requests == [LONG_PARTS[0], LONG_PARTS[1], LONG_PARTS[1], LONG_PARTS[2]]  # часть 1 — один раз
