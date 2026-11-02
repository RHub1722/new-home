"""Страница дашборда на Streamlit (спека §7.2): вкладки «Топ», «Карта», «Рынок» и карточка участка.

Streamlit исполняет dashboard/app.py заново при каждом действии на странице, база каждый раз читается заново.
Путь к settings.yaml приходит в переменной окружения HOMEALIZE_CONFIG (её ставит `homealize dashboard`).
"""

import html
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pydeck as pdk
import streamlit as st
import yaml

from homealize.config import Settings, load_settings
from homealize.dashboard import CONFIG_ENV, listings, market
from homealize.dashboard import card as cards
from homealize.dashboard.listings import Filters, ListingView
from homealize.export.excel import AUTHOR_LABELS
from homealize.notify import format as fmt
from homealize.notify.bot import STATES
from homealize.scoring.components import EXCLUSION_LABELS, FLAG_LABELS, UTILITY_NAMES
from homealize.storage import marks, rate_cache, repo
from homealize.storage.db import connect

DEFAULT_CONFIG = Path("config/settings.yaml")
MIN_SCHEMA = 3  # нужны user_marks (миграция 003)
FLASH = "flash"  # сообщение об отметке: показывается после перезапуска страницы
GENERATION = "generation"  # растёт после каждой отметки: таблица и карта сбрасывают выбор
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+\-.!|$<>~])")


def main() -> None:
    st.set_page_config(page_title="Homealize", page_icon="🏡", layout="wide")
    st.title("🏡 Homealize — участки под строительство")
    settings = _load_settings()
    if settings is None:
        return
    conn = _open_db(settings)
    if conn is None:
        return
    try:
        _page(conn, settings)
    finally:
        conn.close()


def plain(text: str) -> str:
    """Текст с сайта или из базы для st.markdown: разметка Markdown и формулы ($) не срабатывают."""
    return _MARKDOWN.sub(r"\\\1", str(text))


def _load_settings() -> Settings | None:
    path = Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)
    try:
        return load_settings(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        st.error(f"Не удалось прочитать настройки {path}: {exc}")
        return None


def _open_db(settings: Settings) -> sqlite3.Connection | None:
    path = Path(settings.storage.db_path)
    if not path.exists():
        st.error(f"Базы {path} ещё нет — выполните `uv run homealize run`.")
        return None
    conn = connect(path)
    if conn.execute("PRAGMA user_version").fetchone()[0] < MIN_SCHEMA:
        conn.close()
        st.error("Схема базы устарела — выполните `uv run homealize init-db`.")
        return None
    return conn


def _page(conn: sqlite3.Connection, settings: Settings) -> None:
    flash = st.session_state.pop(FLASH, None)
    if flash:
        st.success(flash)
    _status_line(conn, settings)
    rows = listings.load_listings(conn)
    filters = _sidebar(rows)
    shown = listings.apply_filters(rows, filters)
    link_id = st.query_params.get("ad")
    if link_id:
        _card(conn, settings, link_id, key="link", closable=True)
    top, map_tab, market_tab = st.tabs(["Топ", "Карта", "Рынок"])
    with top:
        _top(conn, settings, shown, filters)
    with map_tab:
        _map(conn, settings, shown, filters)
    with market_tab:
        _market(conn, settings)


def _status_line(conn: sqlite3.Connection, settings: Settings) -> None:
    run = repo.last_run(conn)
    if run is None:
        st.caption("Прогонов ещё не было.")
        return
    rated = conn.execute("SELECT COUNT(*) FROM scores WHERE rank IS NOT NULL").fetchone()[0]
    when = fmt.local_time(run["started_at"], settings.schedule.zone)
    st.caption(
        f"Последний прогон: {when}, {STATES.get(run['status'], run['status'])} · в рейтинге {rated} · "
        "после следующего прогона обновите страницу"
    )


def _sidebar(rows: list[ListingView]) -> Filters:
    bar = st.sidebar
    bar.header("Фильтры")
    only_favs = bar.checkbox("Только избранное ★", key="f_favs")
    show_hidden = bar.checkbox("Показать скрытые", key="f_hidden")
    show_suspicious = bar.checkbox("Показать подозрительно дешёвые", key="f_suspicious")
    show_over_budget = bar.checkbox("Показать дороже бюджета", key="f_over_budget")
    limits = listings.bounds(rows)
    price = area = max_minutes = None
    if limits is not None:
        price = _range_slider(bar, "Цена, €", limits.price, step=500.0, number_format="%.0f", key="f_price")
        area = _range_slider(bar, "Площадь, сот.", limits.area, step=0.5, number_format="%.1f", key="f_area")
        if limits.max_minutes > 0:
            max_minutes = bar.slider(
                "До города не дольше, мин", 0.0, limits.max_minutes, limits.max_minutes, step=1.0,
                format="%.0f", key=f"f_minutes_{limits.max_minutes}",
            )
    by_label = {label: name for name, label in UTILITY_NAMES.items()}
    required = bar.multiselect("Обязательно есть", list(by_label), key="f_utilities", placeholder="Не важно")
    options = listings.settlement_options(rows)
    # Список сёл в ключе: если после прогона он изменился, выбор сбрасывается, а не ссылается на пропавшее село.
    settlements = bar.multiselect(
        "Сёла", options, key=f"f_settlements_{hash(tuple(options))}", placeholder="Все сёла"
    )
    return Filters(
        price=price,
        area=area,
        max_minutes=max_minutes,
        utilities=frozenset(by_label[label] for label in required),
        settlements=frozenset(settlements),
        only_favs=only_favs,
        show_hidden=show_hidden,
        show_suspicious=show_suspicious,
        show_over_budget=show_over_budget,
    )


def _range_slider(bar, label: str, limits: tuple[float, float], *, step: float, number_format: str,
                  key: str) -> tuple[float, float] | None:
    low, high = float(limits[0]), float(limits[1])
    if low >= high:
        return None
    # Границы в ключе: после прогона они меняются, и ползунок создаётся заново на всём диапазоне.
    return bar.slider(label, low, high, (low, high), step=step, format=number_format, key=f"{key}_{low}_{high}")


def _top(conn: sqlite3.Connection, settings: Settings, shown: list[ListingView], filters: Filters) -> None:
    if not shown:
        st.info("Под фильтры ничего не подошло.")
        return
    rated = sum(row.rank is not None for row in shown)
    st.caption(f"Показано {len(shown)}, в рейтинге из них {rated}. Нажмите на строку — ниже откроется карточка.")
    frame = _top_frame(shown)
    event = st.dataframe(
        frame,
        key=f"top_{st.session_state.get(GENERATION, 0)}_{hash(filters)}",
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
        column_order=[column for column in frame.columns if column != "id"],
        column_config={
            "Место": st.column_config.NumberColumn(format="%d"),
            "Балл": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "Цена, €": st.column_config.NumberColumn(format="%.0f"),
            "€/сот.": st.column_config.NumberColumn(format="%.0f"),
            "К медиане, %": st.column_config.NumberColumn(format="%+d"),
            "Мин до города": st.column_config.NumberColumn(format="%.0f"),
            "999.md": st.column_config.LinkColumn(display_text="открыть"),
        },
    )
    if event.selection.rows:
        _card(conn, settings, str(frame.iloc[event.selection.rows[0]]["id"]), key="top")


def _top_frame(rows: list[ListingView]) -> pd.DataFrame:
    frame = pd.DataFrame([
        {
            "id": row.listing_id,
            "Место": row.rank,
            "★": "★" if row.fav else "",
            "Балл": row.total,
            "Село": row.settlement,
            "Сотки": row.area_sot,
            "Цена, €": row.price_eur,
            "€/сот.": row.price_per_sot_eur,
            "К медиане, %": row.deviation_percent,
            "Мин до города": row.route_min,
            "Метки": listings.labels(row),
            "999.md": row.url,
        }
        for row in rows
    ])
    frame["Место"] = frame["Место"].astype("Int64")
    frame["К медиане, %"] = frame["К медиане, %"].astype("Int64")
    return frame


def _map(conn: sqlite3.Connection, settings: Settings, shown: list[ListingView], filters: Filters) -> None:
    points = [row for row in shown if row.lat is not None and row.lon is not None]
    if not points:
        st.info("Под фильтры ничего не подошло.")
        return
    st.caption(
        "Цвет точки — балл: красный 0, жёлтый 50, зелёный 100. Синие точки — въезды в Кишинёв. "
        "Нажмите на точку — ниже откроется карточка."
    )
    data = [
        {
            "id": row.listing_id,
            "lat": row.lat,
            "lon": row.lon,
            "color": [*listings.score_color(row.total), 220],
            "title": html.escape(f"{row.total:.0f} · {row.settlement}"),
            "subtitle": html.escape(_map_subtitle(row)),
        }
        for row in points
    ]
    entries = [
        {"lat": point.lat, "lon": point.lon, "title": "Въезд в Кишинёв", "subtitle": html.escape(point.name)}
        for point in settings.routing.entry_points
    ]
    deck = pdk.Deck(
        layers=[
            pdk.Layer(
                "ScatterplotLayer", id="entries", data=entries, get_position=["lon", "lat"],
                get_fill_color=[30, 100, 220, 230], get_radius=150, radius_min_pixels=4, pickable=True,
            ),
            pdk.Layer(
                "ScatterplotLayer", id=listings.LISTINGS_LAYER, data=data, get_position=["lon", "lat"],
                get_fill_color="color", get_radius=120, radius_min_pixels=5, radius_max_pixels=14,
                pickable=True, auto_highlight=True,
            ),
        ],
        initial_view_state=pdk.ViewState(latitude=settings.poi.center_lat, longitude=settings.poi.center_lon, zoom=10),
        map_provider="carto",
        map_style="light",
        tooltip={"html": "<b>{title}</b><br/>{subtitle}"},
    )
    event = st.pydeck_chart(
        deck,
        key=f"map_{st.session_state.get(GENERATION, 0)}_{hash(filters)}",
        on_select="rerun",
        selection_mode="single-object",
        height=560,
    )
    listing_id = listings.picked_listing_id(getattr(event, "selection", None))
    if listing_id:
        _card(conn, settings, listing_id, key="map")


def _map_subtitle(row: ListingView) -> str:
    parts = [fmt.area(row.area_sot), fmt.money(row.price_eur)]
    if row.route_min is not None:
        parts.append(f"{row.route_min:.0f} мин до города")
    return ", ".join(parts)


def _card(conn: sqlite3.Connection, settings: Settings, listing_id: str, *, key: str, closable: bool = False) -> None:
    box = st.container(border=True)
    card = cards.load_card(conn, settings, listing_id)
    if card is None:
        box.warning(f"Участка {plain(listing_id)} нет в оценке: он снят с сайта, отсеян фильтром или ещё не оценён.")
    else:
        _card_body(box, conn, settings, card, key=key)
    if closable and box.button("Закрыть карточку", key=f"{key}_close"):
        del st.query_params["ad"]
        st.rerun()


def _card_body(box, conn: sqlite3.Connection, settings: Settings, card: cards.Card, *, key: str) -> None:
    row, zone = card.row, settings.schedule.zone
    name = row["settlement"] or fmt.NO_SETTLEMENT
    place = f"{name} ({row['region']})" if row["region"] else name
    star = "★ " if card.fav else ""
    box.subheader(plain(f"{star}{place}, {fmt.area(row['area_sot'])} · балл {row['total']:.0f}"))
    if row["rank"] is not None:
        status = f"Место в рейтинге: {row['rank']}"
    else:
        reason = row["excluded_reason"]
        status = f"Не в рейтинге: {EXCLUSION_LABELS.get(reason, reason or 'причина не указана')}"
    lines = [status, fmt.price_line(row), html.unescape(fmt.route_line(row))]
    box.markdown("  \n".join(plain(line) for line in lines))
    if card.photos:
        box.image(card.photos, width=220)
    box.markdown("**Из чего сложился балл**")
    for line in card.components:
        box.progress(
            min(1.0, max(0.0, line.score / 100)),
            text=f"{line.label} {line.score:.0f} × {fmt.decimal(line.weight, 2)} = {fmt.decimal(line.points)}",
        )
        if line.note:
            box.caption(plain(line.note))
    flags = [FLAG_LABELS.get(flag, flag) for flag in json.loads(row["flags"])]
    if flags:
        box.markdown("\n".join(f"- {plain(flag)}" for flag in flags))
    _history(box, card, zone)
    _nearby(box, card, settings)
    author = AUTHOR_LABELS.get(row["author_type"], row["author_type"])
    seen = fmt.local_time(row["first_seen_at"], zone, "%d.%m.%Y")
    box.caption(plain(f"Автор: {author} · впервые увидено {seen}"))
    if row["description"]:
        box.expander("Описание из объявления").text(row["description"])
    left, right = box.columns(2)
    left.link_button("Открыть на 999.md", row["url"])
    if row["lat"] is not None and row["lon"] is not None:
        right.link_button("Карта OpenStreetMap", fmt.osm_url(row["lat"], row["lon"]))
    _mark_buttons(box, conn, settings, card, key=key)


def _history(box, card: cards.Card, zone: ZoneInfo) -> None:
    if len(card.history) > 1:
        frame = pd.DataFrame({
            "Дата": [point.observed_at.astimezone(zone) for point in card.history],
            "Цена": [point.price for point in card.history],
        })
        box.markdown("**История цены**")
        box.line_chart(frame, x="Дата", y="Цена", height=200)
        box.caption(plain(" → ".join(fmt.money(point.price, point.currency) for point in card.history[-6:])))
    elif card.history:
        first = card.history[0]
        since = fmt.local_time(first.observed_at, zone, "%d.%m.%Y")
        box.caption(plain(f"Цена {fmt.money(first.price, first.currency)} не менялась с {since}"))


def _nearby(box, card: cards.Card, settings: Settings) -> None:
    limit = fmt.decimal(settings.scoring.poi_zero_m / 1000).removesuffix(",0")
    if not card.nearby:
        box.caption(f"Инфраструктуры из OpenStreetMap ближе {limit} км не найдено.")
        return
    box.markdown(f"**Рядом (до {limit} км)**")
    box.markdown("\n".join(
        f"- {plain(poi.label)}: {plain(poi.name or 'без названия')}, {poi.distance_m} м" for poi in card.nearby
    ))


def _mark_buttons(box, conn: sqlite3.Connection, settings: Settings, card: cards.Card, *, key: str) -> None:
    listing_id = card.row["listing_id"]
    left, right = box.columns(2)
    if left.button("☆ Убрать из избранного" if card.fav else "★ В избранное", key=f"{key}_fav_{listing_id}"):
        _set_mark(conn, settings, listing_id, marks.FAV, on=not card.fav,
                  done="Убрано из избранного" if card.fav else "Добавлено в избранное ★")
    if right.button("Вернуть в рейтинг" if card.hidden else "🙈 Скрыть", key=f"{key}_hide_{listing_id}"):
        _set_mark(conn, settings, listing_id, marks.HIDDEN, on=not card.hidden,
                  done="Возвращено в рейтинг" if card.hidden
                  else "Скрыто: в рейтинге, уведомлениях и дайджесте его больше не будет")


def _set_mark(conn: sqlite3.Connection, settings: Settings, listing_id: str, mark: str, *, on: bool,
              done: str) -> None:
    try:
        cards.set_mark(conn, settings, listing_id, mark, on=on, now=datetime.now(UTC))
    except cards.MarkBusyError:
        st.warning("Сейчас идёт прогон — отметка не сохранена. Попробуйте через минуту.")
        return
    st.session_state[FLASH] = done
    st.session_state[GENERATION] = st.session_state.get(GENERATION, 0) + 1
    st.rerun()


def _market(conn: sqlite3.Connection, settings: Settings) -> None:
    d = settings.dashboard
    zone = settings.schedule.zone
    now = datetime.now(UTC)
    latest = rate_cache.latest_rates(conn)
    weekly = market.weekly_medians(
        conn, zone=zone, rates=latest[1] if latest else None, now=now, weeks=d.market_weeks, min_n=d.market_min_n
    )
    table = market.market_table(weekly, current=market.week_start(now, zone), compare_weeks=d.market_compare_weeks)
    if not table:
        st.info(f"Пока мало данных: на этой неделе нет сёл, где объявлений хотя бы {d.market_min_n}.")
        return
    st.caption(
        "Медиана €/сотку по объявлениям, прошедшим фильтры типа и площади, включая дороже бюджета. "
        f"Село показывается, если на этой неделе в нём не меньше {d.market_min_n} объявлений."
    )
    change = f"За {d.market_compare_weeks} нед., %"
    st.dataframe(
        pd.DataFrame([
            {
                "Село": row.place.settlement,
                "Район": row.place.region or "",
                "Объявлений": row.n,
                "Медиана €/сот.": row.median,
                change: row.change_percent,
            }
            for row in table
        ]),
        hide_index=True,
        column_config={
            "Медиана €/сот.": st.column_config.NumberColumn(format="%.0f"),
            change: st.column_config.NumberColumn(format="%+.1f"),
        },
    )
    options = [row.place.label for row in table]
    chosen = set(st.multiselect(
        "Сёла на графике", options, default=options[:5], key=f"market_places_{hash(tuple(options))}"
    ))
    series = [item for item in weekly if item.place.label in chosen]
    if not series:
        st.caption("Выберите сёла, чтобы увидеть медиану по неделям.")
        return
    frame = pd.DataFrame([{"Неделя": item.week, "Село": item.place.label, "€/сот.": item.median} for item in series])
    frame["Неделя"] = pd.to_datetime(frame["Неделя"])
    # x задан явно: без него Streamlit сам делает reset_index(inplace=True), а pandas 3.1 предупреждает об этом.
    st.line_chart(frame.pivot(index="Неделя", columns="Село", values="€/сот.").reset_index(), x="Неделя", height=320)
