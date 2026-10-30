from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from homealize.config import Settings
from homealize.models import RunSummary
from homealize.notify import outbox
from homealize.notify.outbox import Notifier, Outgoing
from homealize.notify.telegram import TelegramError
from homealize.runlock import LockBusyError
from homealize.serve import Service
from homealize.storage import repo
from tests.factories import FakeTelegram

ZONE = ZoneInfo("Europe/Chisinau")
SETTINGS = Settings()


def at(day, hour, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=ZONE).astimezone(UTC)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def service(db, now, *, telegram=None, run_job=None, sleeps=None):
    runs = []
    s = Service(
        db, SETTINGS, Notifier(telegram if telegram is not None else FakeTelegram(), 42),
        run_job=run_job or runs.append,
        clock=now if isinstance(now, Clock) else Clock(now),
        sleep=sleeps.append if sleeps is not None else (lambda _seconds: None),
    )
    return s, runs


def ran_at(db, moment):
    run_id = repo.start_run(db, moment)
    repo.finish_run(db, run_id, RunSummary(search_complete=True), now=moment)
    db.commit()


def test_scheduled_run_starts_on_time_and_only_once(db):
    ran_at(db, at(30, 7))
    s, runs = service(db, at(30, 10, 0, 10))
    s.tick()
    s.tick()
    assert runs == [at(30, 10, 0, 10)]


def test_missed_runs_collapse_into_one_catch_up_after_sleep(db):
    ran_at(db, at(30, 7))
    s, runs = service(db, at(30, 17, 40))
    s.tick()
    assert runs == [at(30, 17, 40)]


def test_no_catch_up_right_before_the_next_run(db):
    ran_at(db, at(29, 19))
    clock = Clock(at(30, 6, 30))
    s, runs = service(db, clock)
    s.tick()
    assert runs == []
    clock.now = at(30, 7, 0, 5)
    s.tick()
    assert runs == [at(30, 7, 0, 5)]


def test_poll_waits_until_the_next_event(db):
    ran_at(db, at(30, 10))
    telegram = FakeTelegram()
    service(db, at(30, 12, 59, 40), telegram=telegram)[0].tick()
    service(db, at(30, 11), telegram=telegram)[0].tick()
    assert telegram.polls == [(None, 20), (None, 50)]


def test_busy_or_crashing_run_does_not_stop_the_service(db):
    telegram = FakeTelegram()

    def busy(now):
        raise LockBusyError("run.lock")

    service(db, at(30, 10, 0, 10), telegram=telegram, run_job=busy)[0].tick()
    assert telegram.sent == []

    def boom(now):
        raise RuntimeError("сломалось")

    service(db, at(30, 13, 0, 10), telegram=telegram, run_job=boom)[0].tick()
    assert telegram.texts()[-1].startswith("🛠 <b>Тех. тревога</b>: прогон упал")
    assert "RuntimeError: сломалось" in telegram.texts()[-1]


def test_digest_is_sent_once_in_the_evening(db):
    ran_at(db, at(30, 19))
    telegram = FakeTelegram()
    s, runs = service(db, at(30, 20, 0, 30), telegram=telegram)
    s.tick()
    s.tick()
    assert len([text for text in telegram.texts() if "Дайджест за 30.10" in text]) == 1
    assert runs == []


def test_missed_digest_comes_the_same_night_but_not_next_morning(db):
    ran_at(db, at(30, 22))
    late = FakeTelegram()
    service(db, at(30, 23, 30), telegram=late)[0].tick()
    assert any("Дайджест за 30.10" in text for text in late.texts())
    ran_at(db, at(31, 7))
    morning = FakeTelegram()
    service(db, at(31, 8), telegram=morning)[0].tick()
    assert not any("Дайджест" in text for text in morning.texts())


def test_commands_are_answered_and_the_offset_moves_on(db):
    ran_at(db, at(30, 10))
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/status"}}
    telegram = FakeTelegram(updates=[[update]])
    s, _ = service(db, at(30, 11), telegram=telegram)
    s.tick()
    s.tick()
    assert telegram.texts()[0].startswith("<b>Последний прогон</b>: 30.10 10:00")
    assert "Следующий прогон: 30.10 13:00" in telegram.texts()[0]
    assert [offset for offset, _timeout in telegram.polls] == [None, 6]


def test_poll_failures_back_off(db):
    ran_at(db, at(30, 10))
    telegram = FakeTelegram(updates=[TelegramError("нет сети"), TelegramError("нет сети"), []])
    sleeps = []
    s, _ = service(db, at(30, 11), telegram=telegram, sleeps=sleeps)
    for _ in range(3):
        s.tick()
    assert sleeps == [5, 15]


def test_pending_notifications_are_retried_between_runs(db):
    ran_at(db, at(30, 10))
    outbox.enqueue(db, [Outgoing("alert:x:1", "alert", "тревога", topic="x")], now=at(30, 10, 5))
    db.commit()
    telegram = FakeTelegram()
    service(db, at(30, 10, 30), telegram=telegram)[0].tick()
    assert telegram.texts() == ["тревога"]


def test_start_message(db):
    telegram = FakeTelegram()
    service(db, at(30, 11), telegram=telegram)[0].start()
    assert telegram.texts() == [
        "✅ Homealize запущен. Прогоны: 07:00, 10:00, 13:00, 16:00, 19:00, 22:00, дайджест в 20:00. Команды: /help"
    ]


def test_run_forever_survives_an_unexpected_error(db, caplog):
    telegram = FakeTelegram()
    sleeps = []
    s, _ = service(db, at(30, 11), telegram=telegram, sleeps=sleeps)
    ticks = []

    def tick():
        ticks.append(None)
        if len(ticks) == 1:
            raise RuntimeError("база недоступна")
        raise KeyboardInterrupt

    s.tick = tick
    with pytest.raises(KeyboardInterrupt):
        s.run_forever()
    assert len(ticks) == 2  # после сбоя цикл продолжился
    assert sleeps == [5]
    assert telegram.texts()[0].startswith("✅ Homealize запущен")
    failed = [record for record in caplog.records if "Итерация serve упала" in record.getMessage()]
    assert len(failed) == 1 and failed[0].exc_info is not None


def test_database_error_inside_a_tick_is_survived(db, caplog):
    telegram = FakeTelegram()
    sleeps = []
    s, _ = service(db, at(30, 11), telegram=telegram, sleeps=sleeps)
    db.close()  # любая ошибка базы внутри tick(): сервис не должен умирать молча
    ticks = []
    real_tick = s.tick

    def tick():
        ticks.append(None)
        if len(ticks) == 3:
            raise KeyboardInterrupt
        real_tick()

    s.tick = tick
    with pytest.raises(KeyboardInterrupt):
        s.run_forever()
    assert sleeps == [5, 5]
    assert len([record for record in caplog.records if "Итерация serve упала" in record.getMessage()]) == 2


def test_malformed_update_is_skipped_and_the_next_one_answered(db):
    ran_at(db, at(30, 10))
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/status"}}
    telegram = FakeTelegram(updates=[["мусор", 7, None, update]])
    s, _ = service(db, at(30, 11), telegram=telegram)
    s.tick()
    s.tick()
    assert telegram.texts()[0].startswith("<b>Последний прогон</b>")
    assert [offset for offset, _timeout in telegram.polls] == [None, 6]
