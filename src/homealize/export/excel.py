"""Рейтинг участков в Excel (спека §7.3)."""

import json
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median

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
# Причины, по которым объявление не годится как пример рыночной цены.
NOT_MARKET = {"suspicious_price", "duplicate", "too_far", "no_route", "no_coords", "no_price_eur"}
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
    except PermissionError as exc:
        raise ExportError(f"файл {latest} открыт в другой программе — закройте его и выполните "
                          "`uv run homealize export`") from exc
    today = now.astimezone(UTC).date()
    shutil.copyfile(latest, out_dir / f"homealize_{today.isoformat()}.xlsx")
    _prune(out_dir, today=today, keep_days=settings.keep_days)
    return latest


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
        cell = ws.cell(row=ws.max_row, column=link_column)
        cell.hyperlink = row["url"]
        cell.style = "Hyperlink"
    _format(ws, score_column=2)


def _price_changes_sheet(ws: Worksheet, conn: sqlite3.Connection, *, now: datetime) -> None:
    ws.append(["Дата", "Село", "Сотки", "Было", "Стало", "Валюта", "Изменение, %", "Ссылка"])
    since = (now.astimezone(UTC) - timedelta(days=PRICE_CHANGE_DAYS)).isoformat()
    history = conn.execute(
        "SELECT h.listing_id, h.price_original, h.currency, h.observed_at, l.settlement, l.area_sot, l.url "
        "FROM price_history h JOIN listings l ON l.id = h.listing_id ORDER BY h.listing_id, h.observed_at, h.id"
    ).fetchall()
    previous: dict[str, sqlite3.Row] = {}
    changes = []
    for entry in history:
        before = previous.get(entry["listing_id"])
        previous[entry["listing_id"]] = entry
        if before is None or entry["observed_at"] < since:
            continue
        old, new = before["price_original"], entry["price_original"]
        percent = round((new / old - 1) * 100, 1) if old else None
        changes.append([entry["observed_at"][:10], entry["settlement"], entry["area_sot"], old, new,
                        entry["currency"], percent, entry["url"]])
    for change in sorted(changes, key=lambda c: c[0], reverse=True):
        url = change.pop()
        ws.append([*change, "открыть"])
        cell = ws.cell(row=ws.max_row, column=8)
        cell.hyperlink = url
        cell.style = "Hyperlink"
    _format(ws, score_column=None)


def _medians_sheet(ws: Worksheet, rows: list[sqlite3.Row]) -> None:
    ws.append(["Село", "Район", "Объявлений", "Медиана €/сотка"])
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        if row["price_per_sot_eur"] is None or row["excluded_reason"] in NOT_MARKET:
            continue
        groups[(row["settlement"], row["region"])].append(row["price_per_sot_eur"])
    for (settlement, region), values in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        ws.append([settlement, region, len(values), round(median(values))])
    _format(ws, score_column=None)


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
    for path in out_dir.iterdir():
        match = DAILY_NAME.match(path.name)
        if match and date.fromisoformat(match.group(1)) < today - timedelta(days=keep_days):
            path.unlink()


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None
