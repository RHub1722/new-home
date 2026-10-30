from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.notify import outbox
from homealize.notify.alerts import Failure, plan_alerts

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
SETTINGS = Settings()


def test_alert_has_kind_detail_and_status_hint(db):
    detail = "RateLimitedError: 999.md ограничил частоту запросов"
    items = plan_alerts(db, SETTINGS, [Failure("rate_limited", detail)], run_id=7, now=T0)
    assert [(item.key, item.kind, item.topic) for item in items] == [("alert:rate_limited:7", "alert", "rate_limited")]
    assert items[0].text.split("\n") == [
        "🛠 <b>Тех. тревога</b>: 999.md ограничил частоту запросов — обход остановлен до следующего прогона",
        detail,
        "Подробности: /status",
    ]


def test_same_alert_is_not_repeated_within_six_hours(db):
    outbox.enqueue(db, plan_alerts(db, SETTINGS, [Failure("source_unavailable", "x")], run_id=1, now=T0), now=T0)
    later = T0 + timedelta(hours=5)
    assert plan_alerts(db, SETTINGS, [Failure("source_unavailable", "x")], run_id=2, now=later) == []
    assert len(plan_alerts(db, SETTINGS, [Failure("export_failed", "Excel")], run_id=2, now=later)) == 1
    assert len(plan_alerts(db, SETTINGS, [Failure("source_unavailable", "x")], run_id=3, now=T0 + timedelta(hours=6))) == 1


def test_detail_is_shortened_and_escaped(db):
    detail = "<html>" + "x" * 400 + "\nвторая строка"
    text = plan_alerts(db, SETTINGS, [Failure("crashed", detail)], run_id=None, now=T0)[0].text
    second = text.split("\n")[1]
    assert second.startswith("&lt;html&gt;") and second.endswith("…")
    assert "вторая строка" not in text


def test_alert_without_run_uses_the_time_in_its_key(db):
    items = plan_alerts(db, SETTINGS, [Failure("crashed", "")], run_id=None, now=T0)
    assert items[0].key == f"alert:crashed:{T0.isoformat()}"
    assert items[0].text.split("\n") == ["🛠 <b>Тех. тревога</b>: прогон упал", "Подробности: /status"]
