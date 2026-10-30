from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from homealize.config import ScheduleSettings
from homealize.scheduler import Schedule

ZONE = ZoneInfo("Europe/Chisinau")
SCHEDULE = Schedule(ScheduleSettings())


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Время по Кишинёву в октябре 2026 (UTC+2), переведённое в UTC."""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=ZONE).astimezone(UTC)


def test_last_and_next_run_slots():
    now = at(30, 11, 30)
    assert SCHEDULE.last_run_slot(now) == at(30, 10)
    assert SCHEDULE.next_run_slot(now) == at(30, 13)


def test_night_reaches_back_to_yesterday_and_forward_to_the_morning():
    now = at(30, 3)
    assert SCHEDULE.last_run_slot(now) == at(29, 22)
    assert SCHEDULE.next_run_slot(now) == at(30, 7)


def test_slot_time_itself_belongs_to_that_slot():
    assert SCHEDULE.last_run_slot(at(30, 13)) == at(30, 13)
    assert SCHEDULE.next_run_slot(at(30, 13)) == at(30, 16)


def test_run_is_due_on_time_and_not_twice():
    now = at(30, 10, 0, 20)
    assert SCHEDULE.run_due(now, last_started=at(30, 7))
    assert not SCHEDULE.run_due(now, last_started=at(30, 10, 0, 1))


def test_missed_runs_collapse_into_one_catch_up():
    now = at(30, 17, 40)  # ПК спал с утра, следующий прогон в 19:00
    assert SCHEDULE.run_due(now, last_started=at(30, 7))
    assert not SCHEDULE.run_due(now + timedelta(minutes=1), last_started=now)


def test_no_catch_up_right_before_the_next_slot():
    assert not SCHEDULE.run_due(at(30, 6, 30), last_started=at(29, 19))
    assert SCHEDULE.run_due(at(30, 7, 0, 5), last_started=at(29, 19))


def test_a_few_minutes_late_is_still_on_time():
    schedule = Schedule(ScheduleSettings(run_times=["07:00", "07:30"]))
    assert schedule.run_due(at(30, 7, 3), last_started=None)
    assert not schedule.run_due(at(30, 7, 10), last_started=None)  # догонять поздно: через 20 мин следующий


def test_first_ever_run_is_due():
    assert SCHEDULE.run_due(at(30, 11), last_started=None)


def test_digest_is_due_after_its_time_on_the_same_day_only():
    assert not SCHEDULE.digest_due(at(30, 19, 59), sent_today=False)
    assert SCHEDULE.digest_due(at(30, 23, 10), sent_today=False)
    assert not SCHEDULE.digest_due(at(30, 23, 10), sent_today=True)
    assert SCHEDULE.digest_day(at(31, 0, 30)) == date(2026, 10, 31)
    assert not SCHEDULE.digest_due(at(31, 8), sent_today=False)


def test_next_event_is_the_nearest_of_run_and_digest():
    assert SCHEDULE.next_event(at(30, 19, 30)) == at(30, 20)
    assert SCHEDULE.next_event(at(30, 20, 30)) == at(30, 22)
    assert SCHEDULE.next_event(at(30, 22, 30)) == at(31, 7)


def test_daylight_saving_change_keeps_local_times():
    summer = datetime(2027, 3, 28, 12, 0, tzinfo=UTC)  # 15:00 по Кишинёву, уже летнее время (UTC+3)
    assert SCHEDULE.last_run_slot(summer).astimezone(ZONE).hour == 13
    assert SCHEDULE.next_run_slot(summer).astimezone(ZONE).hour == 16
