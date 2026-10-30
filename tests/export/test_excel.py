import json
from datetime import UTC, datetime, timedelta

import pytest
from openpyxl import load_workbook

from homealize.config import ExportSettings
from homealize.export.excel import ExportError, export_workbook
from homealize.storage import repo
from homealize.storage.scores import replace_scores
from tests.factories import make_listing

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)


def add_listing(db, lid, *, now=T0, **overrides):
    repo.upsert_listing(db, make_listing(id=lid, url=f"https://999.md/ru/{lid}", **overrides),
                        status="active", filter_reason=None, now=now)
    db.commit()


def record(listing_id, **overrides):
    base = {
        "listing_id": listing_id,
        "computed_at": T0.isoformat(),
        "price_eur": 30000.0,
        "price_per_sot_eur": 5000.0,
        "lat": 46.93,
        "lon": 28.94,
        "coords_approx": False,
        "route_km": 8.0,
        "route_min": 10.0,
        "nearest_entry": "M3",
        "poi_nearest_m": "{}",
        "market_median_eur_per_sot": 6000.0,
        "median_level": "settlement",
        "median_n": 6,
        "duplicate_group": None,
        "is_group_primary": True,
        "components": json.dumps({"price": 70.83, "route": 80.0, "infrastructure": 30.0, "utilities": 90.0,
                                  "settlement": 85.0}),
        "explanations": json.dumps({"price": "Цена 71: дешевле", "route": "Дорога 80: 10 мин"}, ensure_ascii=False),
        "flags": json.dumps(["no_sewerage"]),
        "total": 69.7,
        "excluded_reason": None,
        "rank": 1,
    }
    base.update(overrides)
    return base


def sheet_rows(ws):
    header = [cell.value for cell in ws[1]]
    return [dict(zip(header, values)) for values in ws.iter_rows(min_row=2, values_only=True)]


def test_workbook_sheets_and_top_row(db, tmp_path):
    add_listing(db, "a")
    add_listing(db, "b", price_original=60000.0)
    replace_scores(db, [
        record("a"),
        record("b", rank=None, total=40.0, excluded_reason="over_budget", price_eur=60000.0,
               price_per_sot_eur=10000.0),
    ])
    db.commit()
    path = export_workbook(db, ExportSettings(dir=tmp_path / "exports"), now=T0)
    wb = load_workbook(path)
    assert wb.sheetnames == ["Топ", "Все активные", "Изменения цен", "Медианы по сёлам"]
    top = sheet_rows(wb["Топ"])
    assert len(top) == 1
    row = top[0]
    assert (row["Место"], row["Балл"], row["Село"], row["Отклонение, %"]) == (1, 69.7, "Гратиешты", -17)
    assert row["Цена (балл)"] == 70.8
    assert row["Метки"] == "⚠ нет канализации (не указано в объявлении)"
    assert row["Пояснение"] == "Цена 71: дешевле | Дорога 80: 10 мин"
    header = [cell.value for cell in wb["Топ"][1]]
    link = wb["Топ"].cell(row=2, column=header.index("Ссылка") + 1)
    assert link.hyperlink.target == "https://999.md/ru/a"
    every = sheet_rows(wb["Все активные"])
    assert [r["Исключено"] for r in every] == [None, "дороже бюджета"]


def test_price_changes_sheet(db, tmp_path):
    add_listing(db, "a", now=T0 - timedelta(days=40), price_original=33000.0)
    add_listing(db, "a", now=T0 - timedelta(days=5), price_original=30000.0)
    add_listing(db, "a", now=T0 - timedelta(days=1), price_original=27000.0)
    path = export_workbook(db, ExportSettings(dir=tmp_path), now=T0)
    ws = load_workbook(path)["Изменения цен"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert rows == [
        ("2026-10-29", "Гратиешты", 6.0, 30000.0, 27000.0, "EUR", -10.0, "открыть"),
        ("2026-10-25", "Гратиешты", 6.0, 33000.0, 30000.0, "EUR", -9.1, "открыть"),
    ]
    assert ws.cell(row=2, column=8).hyperlink.target == "https://999.md/ru/a"


def test_price_change_with_currency_switch(db, tmp_path):
    add_listing(db, "a", now=T0 - timedelta(days=5), price_original=30000.0)
    add_listing(db, "a", now=T0 - timedelta(days=1), price_original=600000.0, currency="MDL")
    path = export_workbook(db, ExportSettings(dir=tmp_path), now=T0)
    rows = list(load_workbook(path)["Изменения цен"].iter_rows(min_row=2, values_only=True))
    assert rows == [("2026-10-29", "Гратиешты", 6.0, 30000.0, 600000.0, "EUR→MDL", None, "открыть")]


def test_price_changes_skip_filtered_listings(db, tmp_path):
    for now, price in ((T0 - timedelta(days=5), 30000.0), (T0 - timedelta(days=1), 27000.0)):
        repo.upsert_listing(db, make_listing(id="a", url="https://999.md/ru/a", price_original=price),
                            status="filtered_out", filter_reason="not_construction", now=now)
    db.commit()
    path = export_workbook(db, ExportSettings(dir=tmp_path), now=T0)
    assert list(load_workbook(path)["Изменения цен"].iter_rows(min_row=2, values_only=True)) == []


def test_medians_sheet(db, tmp_path):
    add_listing(db, "a")
    add_listing(db, "b")
    add_listing(db, "c", settlement=None, region=None)
    add_listing(db, "d")
    replace_scores(db, [
        record("a", market_median_eur_per_sot=6000.0, median_level="settlement", median_n=6),
        record("b", market_median_eur_per_sot=6000.0, median_level="settlement", median_n=6,
               excluded_reason="over_budget", rank=None),
        record("c", market_median_eur_per_sot=4800.0, median_level="region", median_n=30, rank=2),
        record("d", market_median_eur_per_sot=None, median_level=None, median_n=None,
               excluded_reason="no_route", rank=None),
    ])
    db.commit()
    path = export_workbook(db, ExportSettings(dir=tmp_path), now=T0)
    ws = load_workbook(path)["Медианы по сёлам"]
    assert [cell.value for cell in ws[1]] == ["Село", "Район", "Уровень", "Объявлений в выборке", "Медиана €/сотка"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert rows == [
        ("не указано", "не указано", "весь регион", 30, 4800),
        ("Гратиешты", "Кишинёв мун.", "село", 6, 6000),
    ]


def test_daily_copy_and_pruning(db, tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "homealize_2026-09-01.xlsx").write_bytes(b"old")
    (exports / "homealize_2026-09-30.xlsx").write_bytes(b"exactly keep_days old")
    (exports / "homealize_2026-10-01.xlsx").write_bytes(b"kept")
    (exports / "homealize_2026-10-20.xlsx").write_bytes(b"recent")
    (exports / "homealize_2026-13-01.xlsx").write_bytes(b"unparseable name")
    (exports / "notes.txt").write_text("keep", encoding="utf-8")
    export_workbook(db, ExportSettings(dir=exports, keep_days=30), now=T0)
    assert sorted(p.name for p in exports.iterdir()) == [
        "homealize_2026-10-01.xlsx",
        "homealize_2026-10-20.xlsx",
        "homealize_2026-10-30.xlsx",
        "homealize_2026-13-01.xlsx",
        "latest.xlsx",
        "notes.txt",
    ]


def test_locked_file_gives_export_error(db, tmp_path, monkeypatch):
    def locked(self, filename):
        raise PermissionError(13, "Permission denied", str(filename))

    monkeypatch.setattr("homealize.export.excel.Workbook.save", locked)
    with pytest.raises(ExportError, match="открыт"):
        export_workbook(db, ExportSettings(dir=tmp_path), now=T0)


def test_locked_daily_copy_gives_export_error(db, tmp_path, monkeypatch):
    def locked(src, dst, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr("homealize.export.excel.shutil.copyfile", locked)
    with pytest.raises(ExportError, match="homealize_2026-10-30"):
        export_workbook(db, ExportSettings(dir=tmp_path), now=T0)


def test_prune_skips_locked_files(db, tmp_path, monkeypatch):
    exports = tmp_path / "exports"
    exports.mkdir()
    old = exports / "homealize_2026-09-01.xlsx"
    old.write_bytes(b"old")

    def locked(self, missing_ok=False):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr("pathlib.Path.unlink", locked)
    path = export_workbook(db, ExportSettings(dir=exports, keep_days=30), now=T0)
    assert path == exports / "latest.xlsx"
    assert old.exists()
