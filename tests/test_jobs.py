from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from homealize import jobs
from homealize.config import Settings
from homealize.notify import outbox
from homealize.notify.outbox import Notifier
from homealize.runlock import LockBusyError, hold_lock
from homealize.sources.base import RateLimitedError
from homealize.storage import repo
from tests.factories import FakeSource, FakeTelegram, LockProbeTelegram, make_listing, page

PROJECT_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
OVERPASS = {"elements": [{"type": "node", "id": 1, "lat": 47.1, "lon": 28.82, "tags": {"shop": "supermarket"}}]}
BNM_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><ValCurs Date="30.10.2026">'
    "<Valute ID=\"47\"><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>20.1303</Value></Valute></ValCurs>"
)


def services_client() -> httpx.Client:
    """Поддельные Overpass, BNM и OSRM (10 минут, 8 км до любого въезда)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "overpass-api.de":
            return httpx.Response(200, json=OVERPASS)
        if request.url.host == "www.bnm.md":
            return httpx.Response(200, text=BNM_XML)
        if request.url.host == "router.project-osrm.org":
            n = len(request.url.params["sources"].split(";"))
            k = len(request.url.params["destinations"].split(";"))
            return httpx.Response(
                200, json={"code": "Ok", "durations": [[600.0] * k] * n, "distances": [[8000.0] * k] * n}
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def settings(tmp_path):
    return Settings.model_validate({
        "storage": {"run_lock_path": str(tmp_path / "run.lock")},
        "scoring": {"settlements_path": str(PROJECT_ROOT / "config" / "settlements.yaml")},
        "export": {"dir": str(tmp_path / "exports")},
        "telegram": {"new_min_total": 0, "price_drop_min_total": 0},
    })


def plot(lid: str, **overrides):
    """Участки в 300 м друг от друга, чтобы не склеиться в дубликаты."""
    lat = 47.09 + int(lid) % 10 * 0.003
    return make_listing(id=lid, url=f"https://999.md/ru/{lid}", lat=lat, lon=28.82, **overrides)


def cycle(db, settings, *listings, now, telegram=None, dry_run=False, source=None):
    return jobs.run_cycle(
        db,
        settings,
        source_factory=lambda: source if source is not None else FakeSource([page(*listings)]),
        notifier=Notifier(telegram, 42) if telegram is not None else None,
        now=now,
        clock=lambda: now,
        dry_run=dry_run,
        http_factory=services_client,
        sleep=lambda _seconds: None,
    )


def test_cycle_scores_announces_and_does_not_repeat(db, settings):
    telegram = FakeTelegram()
    first = cycle(db, settings, plot("101"), plot("102"), plot("103"), now=T0, telegram=telegram)
    assert (first.scan.search_complete, first.evaluation.ok) == (True, True)
    assert len(telegram.sent) == 1 and "Первый прогон" in telegram.texts()[0]

    later = T0 + timedelta(hours=3)
    plots = [plot("101", price_original=25000.0), plot("102"), plot("103"), plot("104")]
    second = cycle(db, settings, *plots, now=later, telegram=telegram)
    texts = telegram.texts()[1:]
    assert len(texts) == 2
    assert any("Новый участок" in text and "/ad_104" in text for text in texts)
    assert any("Цена снижена" in text and "было 28 000 €, стало 25 000 € (−11%)" in text for text in texts)
    assert second.delivery.sent == 2
    assert repo.last_run(db)["notified_at"] == later.isoformat()

    cycle(db, settings, *plots, now=T0 + timedelta(hours=6), telegram=telegram)
    assert len(telegram.sent) == 3
    assert {row[0] for row in db.execute("SELECT status FROM notifications")} == {"sent"}


def test_failed_scan_sends_one_alert_per_six_hours(db, settings):
    telegram = FakeTelegram()
    cycle(db, settings, plot("101"), now=T0, telegram=telegram)
    for hours, expected in ((3, 2), (4, 2), (9, 3)):
        source = FakeSource([], fail_at=0, error=RateLimitedError("лимит"))
        result = cycle(db, settings, now=T0 + timedelta(hours=hours), telegram=telegram, source=source)
        assert result.scan.failure == "rate_limited"
        assert len(telegram.sent) == expected
    assert "ограничил частоту запросов" in telegram.texts()[1]


def test_dry_run_sends_nothing_and_keeps_the_window_open(db, settings):
    telegram = FakeTelegram()
    cycle(db, settings, plot("101"), now=T0, telegram=telegram)
    dry = cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=3), telegram=telegram, dry_run=True)
    assert [item.key for item in dry.planned] == ["new:102"]
    assert len(telegram.sent) == 1
    assert db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=6), telegram=telegram)
    assert "/ad_102" in telegram.texts()[-1]


def test_cycle_refuses_to_start_while_another_run_holds_the_lock(db, settings):
    with hold_lock(settings.storage.run_lock_path):
        with pytest.raises(LockBusyError):
            cycle(db, settings, plot("101"), now=T0)
    assert repo.last_run(db) is None


def test_stale_running_run_is_closed_first(db, settings):
    stale = repo.start_run(db, T0 - timedelta(hours=3))
    db.commit()
    cycle(db, settings, plot("101"), now=T0)
    assert db.execute("SELECT status FROM runs WHERE id = ?", (stale,)).fetchone()[0] == "failed"


def test_failed_evaluation_alerts_and_leaves_new_listings_for_later(db, settings, monkeypatch):
    telegram = FakeTelegram()
    cycle(db, settings, plot("101"), now=T0, telegram=telegram)
    real_evaluate = jobs.evaluate

    def broken(*args, **kwargs):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(jobs, "evaluate", broken)
    result = cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=3), telegram=telegram)
    assert result.evaluation.failure.kind == "evaluation_failed"
    assert "оценка не выполнена" in telegram.texts()[-1]
    assert not any("Новый участок" in text for text in telegram.texts())
    assert repo.last_run(db)["notified_at"] is None

    monkeypatch.setattr(jobs, "evaluate", real_evaluate)
    cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=6), telegram=telegram)
    assert any("Новый участок" in text and "/ad_102" in text for text in telegram.texts())


def test_crash_before_the_scan_alerts(db, settings):
    telegram = FakeTelegram()

    def no_source():
        raise RuntimeError("нет сети")

    result = jobs.run_cycle(
        db, settings, source_factory=no_source, notifier=Notifier(telegram, 42), now=T0, clock=lambda: T0,
        http_factory=services_client, sleep=lambda _seconds: None,
    )
    assert (result.scan, result.run_id, result.crash) == (None, None, "RuntimeError: нет сети")
    assert telegram.texts()[0].startswith("🛠 <b>Тех. тревога</b>: прогон упал")


def digest(db, settings, telegram, **kwargs):
    return jobs.send_digest(
        db, settings, Notifier(telegram, 42), now=T0, sleep=lambda _seconds: None, **kwargs
    )


def test_digest_is_queued_but_not_sent_while_a_run_holds_the_lock(db, settings):
    telegram = FakeTelegram()
    with hold_lock(settings.storage.run_lock_path):
        item, added, delivery = digest(db, settings, telegram)
    assert (added, delivery) == (True, None)  # дайджест дня не потерян, но отправит его следующая отправка
    assert telegram.sent == []
    assert outbox.status(db, item.key) == "pending"

    _item, added, delivery = digest(db, settings, telegram)  # прогон закончился
    assert (added, delivery.sent) == (False, 1)
    assert len(telegram.sent) == 1 and "Дайджест за 30.10" in telegram.texts()[0]
    assert outbox.status(db, item.key) == "sent"

    _item, _added, delivery = digest(db, settings, telegram)
    assert delivery.sent == 0 and len(telegram.sent) == 1  # второй раз то же не уходит


def test_digest_is_delivered_under_the_run_lock(db, settings):
    telegram = LockProbeTelegram(settings.storage.run_lock_path)
    _item, _added, delivery = digest(db, settings, telegram)
    assert delivery.sent == 1 and telegram.lock_held == [True]
    with hold_lock(settings.storage.run_lock_path):  # после отправки блокировка свободна
        pass


def test_digest_dry_run_does_not_touch_the_lock_or_the_queue(db, settings):
    telegram = FakeTelegram()
    with hold_lock(settings.storage.run_lock_path):
        item, added, delivery = digest(db, settings, telegram, dry_run=True)
    assert (added, delivery) == (False, None) and "Дайджест за" in item.text
    assert outbox.status(db, item.key) is None


def test_failed_first_scan_does_not_use_up_the_first_run_summary(db, settings):
    telegram = FakeTelegram()
    source = FakeSource([], fail_at=0, error=RateLimitedError("лимит"))
    cycle(db, settings, now=T0, telegram=telegram, source=source)  # ничего не загружено: только тревога
    assert not any("Первый прогон" in text for text in telegram.texts())

    cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=3), telegram=telegram)
    summaries = [text for text in telegram.texts() if "Первый прогон" in text]
    assert len(summaries) == 1 and "загружено 2 участка" in summaries[0]
