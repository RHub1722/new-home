"""Когда пора делать прогон и дайджест (спека §4 «Расписание», §13). Время по местному часовому поясу.

Пропущенные прогоны (ПК спал или был выключен) схлопываются в один догоняющий; его не делают, если
следующий по расписанию ближе catch_up_gap_minutes. Пропущенный дайджест отправляется до конца того же дня.
"""

from datetime import UTC, date, datetime, time, timedelta

from homealize.config import ScheduleSettings, parse_hhmm

DAY = timedelta(days=1)
ON_TIME = timedelta(minutes=5)  # прогон, опоздавший не больше, считается сделанным вовремя


class Schedule:
    def __init__(self, settings: ScheduleSettings) -> None:
        self.zone = settings.zone
        self._run_times = [parse_hhmm(value) for value in settings.run_times]
        self._digest_time = parse_hhmm(settings.digest_time)
        self._gap = timedelta(minutes=settings.catch_up_gap_minutes)

    def last_run_slot(self, now: datetime) -> datetime:
        """Последнее время прогона по расписанию, не позже now."""
        today = self.digest_day(now)
        return max(slot for day in (today - DAY, today) for slot in self._slots(day, self._run_times) if slot <= now)

    def next_run_slot(self, now: datetime) -> datetime:
        """Ближайшее время прогона по расписанию позже now."""
        today = self.digest_day(now)
        return min(slot for day in (today, today + DAY) for slot in self._slots(day, self._run_times) if slot > now)

    def run_due(self, now: datetime, last_started: datetime | None) -> bool:
        slot = self.last_run_slot(now)
        if last_started is not None and last_started >= slot:
            return False
        if now - slot <= ON_TIME:
            return True
        return self.next_run_slot(now) - now >= self._gap  # догоняющий — только если до следующего далеко

    def digest_day(self, now: datetime) -> date:
        """Местная дата: по ней называется дайджест."""
        return now.astimezone(self.zone).date()

    def digest_at(self, day: date) -> datetime:
        return self._slots(day, [self._digest_time])[0]

    def digest_due(self, now: datetime, *, sent_today: bool) -> bool:
        return not sent_today and now >= self.digest_at(self.digest_day(now))

    def next_event(self, now: datetime) -> datetime:
        """Ближайшее будущее дело: прогон или дайджест."""
        today = self.digest_day(now)
        digests = [moment for moment in (self.digest_at(today), self.digest_at(today + DAY)) if moment > now]
        return min([self.next_run_slot(now), *digests])

    def _slots(self, day: date, times: list[time]) -> list[datetime]:
        return [datetime.combine(day, moment, tzinfo=self.zone).astimezone(UTC) for moment in times]
