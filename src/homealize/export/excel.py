"""Рейтинг участков в Excel (спека §7.3)."""

import json
import re
import shutil
import sqlite3
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from homealize.config import ExportSettings
from homealize.scoring.components import EXCLUSION_LABELS, FLAG_LABELS
from homealize.storage.scores import load_scored

DAILY_NAME = re.compile(r"^homealize_(\d{4}-\d{2}-\d{2})\.xlsx$")
AUTHOR_LABELS = {"private": "частное лицо", "agency": "агентство", "developer": "застройщик", "unknown": "не указан"}
LISTING_HEADERS = [
    "Место", "Балл", "Село", "Район", "Сотки", "Цена, €", "€/сотка", "Медиана €/сот.", "Отклонение, %",
    "Мин до города", "Км до города", "Въезд", "Цена (балл)", "Дорога (балл)", "Инфраструктура (балл)",
    "Коммуникации (балл)", "Село (балл)", "Метки", "Пояснение", "Автор", "Впервые увидено", "Ссылка",
]
COMPONENT_ORDER = ["price", "route", "infrastructure", "utilities", "settlement"]
PRICE_CHANGE_HEADERS = ["Дата", "Село", "Сотки", "Было", "Стало", "Валюта", "Изменение, %", "Ссылка"]
MEDIAN_HEADERS = ["Село", "Район", "Уровень", "Объявлений в выборке", "Медиана €/сотка"]
LEVEL_LABELS = {"settlement": "село", "route_band": "по расстоянию", "region": "весь регион"}
NOT_SPECIFIED = "не указано"
PRICE_CHANGE_DAYS = 30


class ExportError(Exception):
    """Excel не удалось сохранить."""


def export_workbook(conn: sqlite3.Connection, settings: ExportSettings, *, now: datetime) -> Path:
    rows = load_scored(conn)
    workbook = Workbook()
    top = workbook.active
    top.title = "Топ"
    _listing_sheet(top, [row for row in rows if row["rank"] is not None], with_reason=False)
    _listing_sheet(workbook.create_sheet("Все активные"), rows, with_reason=True)
    _price_changes_sheet(workbook.create_sheet("Изменения цен"), conn, now=now)
    _medians_sheet(workbook.create_sheet("Медианы по сёлам"), rows)

    out_dir = Path(settings.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "latest.xlsx"
    try:
        workbook.save(latest)
    except OSError as exc:
        raise _unavailable(latest) from exc
    today = now.astimezone(UTC).date()
    daily = out_dir / f"homealize_{today.isoformat()}.xlsx"
    try:
        shutil.copyfile(latest, daily)
    except OSError as exc:
        raise _unavailable(daily) from exc
    _prune(out_dir, today=today, keep_days=settings.keep_days)
    return latest


def _unavailable(path: Path) -> ExportError:
    return ExportError(f"файл {path} открыт в другой программе или недоступен — закройте его и выполните "
                       "`uv run homealize export`")


def _listing_sheet(ws: Worksheet, rows: list[sqlite3.Row], *, with_reason: bool) -> None:
    headers = LISTING_HEADERS + (["Исключено"] if with_reason else [])
    ws.append(headers)
    link_column = LISTING_HEADERS.index("Ссылка") + 1
    for row in rows:
        components = json.loads(row["components"])
        notes = json.loads(row["explanations"])
        flags = json.loads(row["flags"])
        price_per_sot, market = row["price_per_sot_eur"], row["market_median_eur_per_sot"]
        deviation = round((price_per_sot / market - 1) * 100) if price_per_sot and market else None
        values = [
            row["rank"], row["total"], row["settlement"], row["region"], row["area_sot"], row["price_eur"],
            price_per_sot, market, deviation, row["route_min"], row["route_km"], row["nearest_entry"],
            *[_round(components.get(name)) for name in COMPONENT_ORDER],
            "; ".join(FLAG_LABELS.get(flag, flag) for flag in flags),
            " | ".join(notes.values()),
            AUTHOR_LABELS.get(row["author_type"], row["author_type"]),
            (row["first_seen_at"] or "")[:10],
            "открыть",
        ]
        if with_reason:
            reason = row["excluded_reason"]
            values.append(EXCLUSION_LABELS.get(reason, reason) if reason else None)
        ws.append(values)
        _link(ws, ws.max_row, link_column, row["url"])
    _format(ws, score_column=2)


def _price_changes_sheet(ws: Worksheet, conn: sqlite3.Connection, *, now: datetime) -> None:
    ws.append(PRICE_CHANGE_HEADERS)
    link_column = PRICE_CHANGE_HEADERS.index("Ссылка") + 1
    since = (now.astimezone(UTC) - timedelta(days=PRICE_CHANGE_DAYS)).isoformat()
    history = conn.execute(
        "SELECT h.listing_id, h.price_original, h.price_eur, h.currency, h.observed_at, "
        "l.settlement, l.area_sot, l.url "
        "FROM price_history h JOIN listings l ON l.id = h.listing_id "
        "WHERE l.filter_reason IS NULL ORDER BY h.listing_id, h.observed_at, h.id"
    ).fetchall()
    previous: dict[str, sqlite3.Row] = {}
    changes = []
    for entry in history:
        before = previous.get(entry["listing_id"])
        previous[entry["listing_id"]] = entry
        if before is None or entry["observed_at"] < since:
            continue
        old, new = before["price_original"], entry["price_original"]
        if before["currency"] == entry["currency"]:
            currency, percent = entry["currency"], _percent(old, new)
        else:
            # Между валютами сравниваем цены в евро; пока курс не известен, процент не считаем.
            currency = f"{before['currency']}→{entry['currency']}"
            percent = _percent(before["price_eur"], entry["price_eur"])
        changes.append(([entry["observed_at"][:10], entry["settlement"], entry["area_sot"], old, new, currency,
                         percent], entry["url"]))
    for values, url in sorted(changes, key=lambda change: change[0][0], reverse=True):
        ws.append([*values, "открыть"])
        _link(ws, ws.max_row, link_column, url)
    _format(ws, score_column=None)


def _medians_sheet(ws: Worksheet, rows: list[sqlite3.Row]) -> None:
    """Медианы из снимка оценки: одна строка на каждую рыночную медиану, которую использовала оценка."""
    ws.append(MEDIAN_HEADERS)
    distinct = {
        (row["settlement"], row["region"], row["median_level"], row["market_median_eur_per_sot"], row["median_n"])
        for row in rows
        if row["market_median_eur_per_sot"] is not None
    }
    table = [
        (_placeholder(settlement), _placeholder(region), LEVEL_LABELS.get(level, level), n, round(median_value))
        for settlement, region, level, median_value, n in distinct
    ]
    for settlement, region, level, n, median_value in sorted(table, key=lambda r: (-r[3], r[0], r[1], r[4])):
        ws.append([settlement, region, level, n, median_value])
    _format(ws, score_column=None)


def _link(ws: Worksheet, row: int, column: int, url: str) -> None:
    cell = ws.cell(row=row, column=column)
    cell.hyperlink = url
    cell.style = "Hyperlink"


def _format(ws: Worksheet, *, score_column: int | None) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for index, column in enumerate(ws.iter_cols(min_row=1, max_row=min(ws.max_row, 200)), start=1):
        width = max((len(str(cell.value)) for cell in column if cell.value is not None), default=8)
        ws.column_dimensions[get_column_letter(index)].width = min(60, width + 2)
    if score_column and ws.max_row >= 2:
        letter = get_column_letter(score_column)
        ws.conditional_formatting.add(
            f"{letter}2:{letter}{ws.max_row}",
            ColorScaleRule(start_type="num", start_value=0, start_color="F8696B",
                           mid_type="num", mid_value=50, mid_color="FFEB84",
                           end_type="num", end_value=100, end_color="63BE7B"),
        )


def _prune(out_dir: Path, *, today: date, keep_days: int) -> None:
    # Уборка best-effort: чужие имена пропускаются, файл, который не удалось стереть, остаётся на месте.
    cutoff = today - timedelta(days=keep_days)
    for path in out_dir.iterdir():
        match = DAILY_NAME.match(path.name)
        if not match:
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day <= cutoff:
            with suppress(OSError):
                path.unlink()


def _percent(old: float | None, new: float | None) -> float | None:
    if not old or new is None:
        return None
    return round((new / old - 1) * 100, 1)


def _placeholder(value: str | None) -> str:
    return NOT_SPECIFIED if value is None else value


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None
