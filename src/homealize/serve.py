"""`homealize serve`: прогоны по расписанию и команды бота в одном процессе (спека §4, §13).

Один поток: сделать просроченное (прогон, дайджест), затем ждать сообщений боту long polling до
следующего дела. Во время прогона (1–2 минуты) команды ждут и выполняются после него.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from html import escape

from homealize.config import Settings
from homealize.jobs import send_digest
from homealize.notify import outbox
from homealize.notify.bot import handle_update
from homealize.notify.digest import digest_key
from homealize.notify.format import short
from homealize.notify.outbox import Notifier, deliver
from homealize.notify.telegram import TelegramError
from homealize.runlock import LockBusyError
from homealize.scheduler import Schedule
from homealize.storage import repo

log = logging.getLogger(__name__)

POLL_RETRY_SECONDS = (5, 15, 60)  # пауза после неудачного опроса Telegram: первая, вторая, дальше
RETRY_PENDING = timedelta(minutes=15)  # как часто досылать очередь между прогонами


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Service:
    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        notifier: Notifier,
        *,
        run_job: Callable[[datetime], object],
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._conn = conn
        self._settings = settings
        self._notifier = notifier
        self._schedule = Schedule(settings.schedule)
        self._run_job = run_job
        self._clock = clock
        self._sleep = sleep
        self._offset: int | None = None
        self._handled_slot: datetime | None = None  # слот, по которому прогон уже запускали (или пропустили)
        self._digest_tried: date | None = None
        self._last_retry: datetime | None = None
        self._poll_failures = 0

    def start(self) -> None:
        """Сообщение о запуске: по нему видно, что автозапуск сработал."""
        schedule = self._settings.schedule
        self._send(
            f"✅ Homealize запущен. Прогоны: {', '.join(schedule.run_times)}, дайджест в {schedule.digest_time}. "
            "Команды: /help"
        )

    def run_forever(self) -> None:
        """Работает, пока не остановят (KeyboardInterrupt); неожиданная ошибка итерации не убивает службу."""
        self.start()
        while True:
            try:
                self.tick()
            except Exception:  # под pythonw (автозапуск) консоли нет: без записи в лог сбой был бы невидим
                self._rollback()
                log.exception("Итерация serve упала")
                self._sleep(POLL_RETRY_SECONDS[0])

    def tick(self) -> None:
        """Одна итерация: просроченный прогон, дайджест, досылка очереди, ожидание команд."""
        now = self._clock()
        slot = self._schedule.last_run_slot(now)
        if slot != self._handled_slot and self._schedule.run_due(now, self._last_started()):
            self._handled_slot = slot
            self._do_run(now)
        now = self._clock()
        day = self._schedule.digest_day(now)
        sent_today = outbox.exists(self._conn, digest_key(day))
        if day != self._digest_tried and self._schedule.digest_due(now, sent_today=sent_today):
            self._digest_tried = day
            self._do_digest(now)
        self._retry_pending(self._clock())
        self._poll()

    def _do_run(self, now: datetime) -> None:
        try:
            self._run_job(now)
        except LockBusyError:
            log.info("Прогон по расписанию пропущен: идёт другой прогон")
        except Exception as exc:
            self._rollback()
            log.exception("Прогон по расписанию упал")
            self._send(f"🛠 <b>Тех. тревога</b>: прогон упал\n{escape(short(f'{type(exc).__name__}: {exc}'))}")

    def _do_digest(self, now: datetime) -> None:
        try:
            send_digest(self._conn, self._settings, self._notifier, now=now, sleep=self._sleep)
        except Exception:
            self._rollback()
            log.exception("Дайджест не отправлен")

    def _retry_pending(self, now: datetime) -> None:
        if self._last_retry is not None and now - self._last_retry < RETRY_PENDING:
            return
        self._last_retry = now
        if not outbox.pending_count(self._conn):
            return
        try:
            deliver(self._conn, self._notifier, self._settings.telegram, now=now, sleep=self._sleep)
        except Exception:
            self._rollback()
            log.exception("Очередь уведомлений не отправлена")

    def _poll(self) -> None:
        now = self._clock()
        wait = (self._schedule.next_event(now) - now).total_seconds()
        timeout = int(max(1, min(self._settings.telegram.poll_timeout_seconds, wait)))
        try:
            updates = self._notifier.client.get_updates(self._offset, timeout)
        except TelegramError as exc:
            delay = POLL_RETRY_SECONDS[min(self._poll_failures, len(POLL_RETRY_SECONDS) - 1)]
            self._poll_failures += 1
            log.warning("Telegram: %s — повтор через %s с", exc, delay)
            self._sleep(delay)
            return
        self._poll_failures = 0
        for update in updates:
            if not isinstance(update, dict):
                log.warning("Telegram прислал не сообщение, а %s — пропущено", type(update).__name__)
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = update_id + 1
            try:
                moment = self._clock()
                handle_update(self._conn, self._settings, self._notifier, update, now=moment,
                              next_run=self._schedule.next_run_slot(moment))
            except Exception:
                self._rollback()
                log.exception("Сообщение боту не обработано")

    def _rollback(self) -> None:
        """Откат после сбоя; сам сбой базы (закрытое или сломанное соединение) службу не останавливает."""
        try:
            self._conn.rollback()
        except sqlite3.Error:
            log.exception("Откат транзакции не удался")

    def _last_started(self) -> datetime | None:
        run = repo.last_run(self._conn)
        return datetime.fromisoformat(run["started_at"]) if run is not None else None

    def _send(self, text: str) -> None:
        try:
            self._notifier.client.send_message(self._notifier.chat_id, text)
        except TelegramError as exc:
            log.warning("Сообщение не отправлено: %s", exc)
