import sqlite3
from datetime import UTC, datetime

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from homealize.dashboard import APP_PATH, CONFIG_ENV
from homealize.dashboard.ui import plain
from homealize.runlock import hold_lock
from homealize.storage import marks, repo
from homealize.storage.db import connect, migrate
from homealize.storage.scores import apply_hidden, replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
FIRST = "100000001"


def write_settings(tmp_path, db_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"storage:\n  db_path: '{db_path.as_posix()}'\n"
        f"  run_lock_path: '{(tmp_path / 'data' / 'run.lock').as_posix()}'\n",
        encoding="utf-8",
    )
    return cfg


@pytest.fixture
def site(tmp_path, monkeypatch):
    """База с двумя участками в рейтинге и одним дороже бюджета; HOMEALIZE_CONFIG указывает на её настройки."""
    db_path = tmp_path / "data" / "test.db"
    conn = connect(db_path)
    migrate(conn)
    for listing_id, settlement in ((FIRST, "Сынжера"), ("100000002", "Ватра"), ("100000003", "Бачой")):
        listing = make_listing(id=listing_id, settlement=settlement, url=f"https://999.md/ru/{listing_id}")
        repo.upsert_listing(conn, listing, status="active", filter_reason=None, now=T0)
    replace_scores(conn, [
        score_record(FIRST, total=80.0, rank=1),
        score_record("100000002", total=70.0, rank=2),
        score_record("100000003", total=90.0, rank=None, excluded_reason="over_budget", price_eur=60000.0),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setenv(CONFIG_ENV, str(write_settings(tmp_path, db_path)))
    return tmp_path


def page(**query) -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    for name, value in query.items():
        at.query_params[name] = value
    return at.run()


def settlements(at) -> list[str]:
    return list(at.dataframe[0].value["Село"])


def stored(site, mark):
    conn = connect(site / "data" / "test.db")
    try:
        return marks.marked(conn, mark)
    finally:
        conn.close()


def table_key(at) -> str:
    (key,) = [name for name in at.session_state.keys() if name.startswith("top_")]
    return key


def select_row(at, row: int) -> None:
    """Выбор строки в таблице «Топ»: AppTest не кликает по таблице, поэтому пишет выбор в состояние виджета."""
    at.session_state[table_key(at)] = {"selection": {"rows": [row], "columns": [], "cells": []}}


def hide_directly(site, listing_id):
    """Скрытие из бота (/hide): страница об этом не знает, пока её не перезапустят."""
    conn = connect(site / "data" / "test.db")
    try:
        marks.add_mark(conn, listing_id, marks.HIDDEN, now=T0)
        apply_hidden(conn, marks.marked(conn, marks.HIDDEN))
        conn.commit()
    finally:
        conn.close()


def swap_places(site):
    """Новый прогон поменял первые два места: в таблице те же строки, но в другом порядке."""
    conn = connect(site / "data" / "test.db")
    try:
        replace_scores(conn, [
            score_record(FIRST, total=70.0, rank=2),
            score_record("100000002", total=80.0, rank=1),
            score_record("100000003", total=90.0, rank=None, excluded_reason="over_budget", price_eur=60000.0),
        ])
        conn.commit()
    finally:
        conn.close()


def test_page_shows_the_ranking_in_tabs(site):
    at = page()
    assert not at.exception
    assert [tab.label for tab in at.tabs] == ["Топ", "Карта", "Рынок"]
    assert settlements(at) == ["Сынжера", "Ватра"]
    assert any("Прогонов ещё не было" in caption.value for caption in at.caption)


def test_over_budget_switch_adds_rows(site):
    at = page()
    at.sidebar.checkbox(key="f_over_budget").check().run()
    assert settlements(at) == ["Сынжера", "Ватра", "Бачой"]
    assert "дороже бюджета" in list(at.dataframe[0].value["Метки"])[2]


def test_link_opens_the_card_and_fav_is_saved(site):
    at = page(ad=FIRST)
    assert any("Сынжера" in header.value for header in at.subheader)
    at.button(key=f"link_fav_{FIRST}").click().run()
    assert not at.exception
    assert any("избранное" in message.value for message in at.success)
    assert stored(site, marks.FAV) == {FIRST}
    assert list(at.dataframe[0].value["★"]) == ["★", ""]


def test_hide_from_the_card_removes_the_row(site):
    at = page(ad=FIRST)
    at.button(key=f"link_hide_{FIRST}").click().run()
    assert stored(site, marks.HIDDEN) == {FIRST}
    assert settlements(at) == ["Ватра"]


def test_mark_is_refused_while_a_run_is_going(site):
    at = page(ad=FIRST)
    with hold_lock(site / "data" / "run.lock"):
        at.button(key=f"link_fav_{FIRST}").click().run()
    assert any("идёт прогон" in warning.value for warning in at.warning)
    assert stored(site, marks.FAV) == set()


def test_unknown_link_id_warns(site):
    at = page(ad="999")
    assert not at.exception
    assert any("нет в оценке" in warning.value for warning in at.warning)


def test_close_button_removes_the_link_card(site):
    at = page(ad=FIRST)
    at.button(key="link_close").click().run()
    assert "ad" not in at.query_params
    assert not at.subheader


def test_table_selection_opens_the_card_and_survives_a_rerun(site):
    at = page()
    select_row(at, 1)
    at.run()
    assert [header.value for header in at.subheader] == [r"Ватра \(Кишинёв мун\.\), 6 сот\. · балл 70"]
    at.run()  # данные те же, значит и выбор остаётся
    assert len(at.subheader) == 1


def test_stale_selection_after_a_hide_from_the_bot_does_not_break_the_page(site):
    at = page()
    select_row(at, 1)
    at.run()
    assert at.subheader
    hide_directly(site, FIRST)  # в таблице осталась одна строка, а выбрана вторая
    at.run()
    assert not at.exception
    assert settlements(at) == ["Ватра"]
    assert not at.subheader  # выбор относился к прежней таблице и сброшен


def test_stale_selection_after_a_new_run_does_not_open_another_listing(site):
    at = page()
    select_row(at, 0)
    at.run()
    assert any("Сынжера" in header.value for header in at.subheader)
    swap_places(site)  # на первом месте теперь Ватра, а выбрана была строка 0
    at.run()
    assert not at.exception
    assert settlements(at) == ["Ватра", "Сынжера"]
    assert not at.subheader


def test_market_leaves_the_change_column_empty_without_history(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "market.db"
    conn = connect(db_path)
    migrate(conn)
    now = datetime.now(UTC)  # данные этой недели, в какой бы день ни шли тесты
    records = []
    for index in range(3):
        listing_id = f"20000000{index}"
        listing = make_listing(id=listing_id, settlement="Сынжера", url=f"https://999.md/ru/{listing_id}")
        repo.upsert_listing(conn, listing, status="active", filter_reason=None, now=now)
        records.append(score_record(listing_id, rank=index + 1))
    replace_scores(conn, records)
    conn.commit()
    conn.close()
    monkeypatch.setenv(CONFIG_ENV, str(write_settings(tmp_path, db_path)))
    at = page()
    assert not at.exception
    table = at.dataframe[1].value  # первая таблица — «Топ», вторая — «Рынок»
    assert table["Село"].iloc[0] == "Все сёла"
    change = table["За 4 нед., %"]
    # Сравнивать пока не с чем. Столбец из одних None стал бы object, и NumberColumn вывел бы в ячейках «None».
    assert pd.api.types.is_float_dtype(change)
    assert change.isna().all()


def test_missing_database_is_explained(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, str(write_settings(tmp_path, tmp_path / "data" / "none.db")))
    at = page()
    assert not at.exception
    assert any("Базы" in error.value for error in at.error)


def test_old_schema_is_explained(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "old.db"
    db_path.parent.mkdir()
    sqlite3.connect(db_path).close()  # пустая база, схема v0
    monkeypatch.setenv(CONFIG_ENV, str(write_settings(tmp_path, db_path)))
    at = page()
    assert any("init-db" in error.value for error in at.error)


def test_plain_escapes_markdown():
    assert plain("a*b_$1.5 [x](y)") == r"a\*b\_\$1\.5 \[x\]\(y\)"
