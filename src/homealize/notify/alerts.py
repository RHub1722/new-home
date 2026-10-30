"""Технические тревоги (спека §7.1): сбой обхода, оценки или Excel.

Тревога одного вида приходит не чаще telegram.alert_repeat_hours. Предупреждения внешних сервисов
(Overpass, OSRM, BNM) тревогой не считаются: их видно в /status.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

from homealize.config import Settings
from homealize.notify import format as fmt
from homealize.notify import outbox
from homealize.notify.outbox import Outgoing

ALERT_TEXTS = {
    "rate_limited": "999.md ограничил частоту запросов — обход остановлен до следующего прогона",
    "empty_search": "поиск 999.md вернул 0 объявлений — возможно, сайт изменился",
    "source_unavailable": "999.md недоступен — обход не завершён",
    "source_schema": "ответ 999.md не похож на ожидаемый — возможно, сайт изменился",
    "unparsed": "слишком много объявлений не удалось разобрать — снятие пропущено",
    "too_few_seen": "обход увидел слишком мало объявлений — снятие пропущено",
    "crashed": "прогон упал",
    "evaluation_failed": "оценка не выполнена",
    "export_failed": "Excel не сохранён",
}


@dataclass(frozen=True)
class Failure:
    kind: str  # ключ ALERT_TEXTS
    detail: str  # исходный текст ошибки; в сообщение попадает его первая строка


def plan_alerts(
    conn: sqlite3.Connection, settings: Settings, failures: list[Failure], *, run_id: int | None, now: datetime
) -> list[Outgoing]:
    repeat = timedelta(hours=settings.telegram.alert_repeat_hours)
    stamp = str(run_id) if run_id is not None else now.astimezone(UTC).isoformat()
    items = []
    for failure in failures:
        last = outbox.last_created(conn, "alert", failure.kind)
        if last is not None and now - last < repeat:
            continue
        lines = [f"🛠 <b>Тех. тревога</b>: {escape(ALERT_TEXTS.get(failure.kind, failure.kind))}"]
        if failure.detail:
            lines.append(escape(fmt.short(failure.detail)))
        lines.append("Подробности: /status")
        items.append(
            Outgoing(key=f"alert:{failure.kind}:{stamp}", kind="alert", topic=failure.kind, text="\n".join(lines))
        )
    return items
