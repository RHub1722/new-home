# Этап 4 «Дашборд» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** веб-дашборд на Streamlit: рейтинг участков с фильтрами, карта, карточка участка с отметками ★ и «скрыть», рынок по сёлам. Он стартует вместе с автозапуском Windows и открывается с телефона в домашней Wi-Fi.

**Architecture:**
- Данные и правила — в модулях без Streamlit, их проверяют обычные тесты:
  - `dashboard/listings.py` — строки рейтинга, фильтры, метки, цвет балла;
  - `dashboard/card.py` — карточка и отметки;
  - `dashboard/market.py` — медианы по неделям.
- `dashboard/ui.py` рисует страницу из этих данных, а `dashboard/app.py` — файл, который исполняет `streamlit run`. Страницу проверяет `streamlit.testing.v1.AppTest`.
- `homealize dashboard` запускает Streamlit в том же процессе. Автозапуск получает вторую задачу Планировщика «Homealize Dashboard».

**Tech Stack:** Python 3.12, uv, Streamlit 1.66 (новая зависимость), pydeck 0.9 (карта, подложка CARTO без ключа), pandas, sqlite3, pydantic v2, pytest + Streamlit AppTest. PowerShell для Планировщика.

**Spec:** `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md`: этап 4 из §11, дашборд §7.2, данные §5, рынок §2 и §6.1. Уточнения этого плана записываются в спеку новым §14 (задача 5).

**Предыдущие этапы:** этапы 1–3 в `main` (468 офлайн-тестов). Отложенные замечания лежат в `docs/superpowers/notes/` (`2026-10-29-stage1-followups.md`, `2026-10-30-stage2-followups.md`, `2026-10-30-stage3-followups.md`). Дашборда из них ничего не касается, поэтому этот этап их не закрывает. Проверка WAL на томах Docker остаётся на этап 5.

## Решения, принятые при подготовке плана

Решения 1 и 2 выбрал пользователь, остальные приняты при планировании. Они обязательны для исполнителей.

1. **Запуск вместе с автозапуском** (выбор пользователя).
   - `scripts/autostart.ps1 -Install` регистрирует вторую задачу Планировщика «Homealize Dashboard»: `.venv\Scripts\pythonw.exe -m homealize.cli dashboard`, при входе пользователя, без окна и без ограничения времени.
   - `-Remove` удаляет обе задачи.
   - Вручную дашборд запускается так: `uv run homealize dashboard`.
2. **Доступ с телефона по Wi-Fi** (выбор пользователя; пароля нет — пользователь согласился).
   - Адрес задаётся в `dashboard.host`. По умолчанию в коде `127.0.0.1`, а в `config/settings.yaml` стоит `0.0.0.0`.
   - Windows пропускает входящие соединения только в частных сетях: правило брандмауэра создаётся для частных сетей. Как это сделать, описано в README (задача 5); скрипт автозапуска брандмауэр не трогает.
3. **Streamlit в том же процессе.**
   - `homealize dashboard` ставит переменную окружения `HOMEALIZE_CONFIG` (путь к settings.yaml) и вызывает `streamlit.web.cli.main()` с `sys.argv = ["streamlit", "run", <app.py>, …]`.
   - Флаги: `--server.address`, `--server.port`, `--server.headless true`, `--server.fileWatcherType none`, `--server.runOnSave false`, `--browser.gatherUsageStats false`, `--client.toolbarMode viewer`.
   - Если порт занят, команда выходит с кодом 4 и пишет, что дашборд уже запущен.
   - Под pythonw у процесса нет `sys.stdout` и `sys.stderr`, поэтому они перенаправляются в `logs/dashboard.log` (без ротации). В `logs/homealize.log` дашборд не пишет: этот файл держит открытым serve, и ротация двух процессов на Windows конфликтует.
4. **Данные читаются заново при каждом действии на странице.** Каждый раз открывается новое соединение SQLite (WAL позволяет читать во время прогона), кэша нет: на 1 650 строк это десятки миллисекунд. Нужна схема не ниже v3 (`user_marks`). Если базы нет или схема старая, страница показывает понятную ошибку.
5. **Топ.**
   - В таблице по умолчанию только рейтинг.
   - Переключатели добавляют скрытые, подозрительно дешёвые и дороже бюджета.
   - Остальные исключённые (далеко, нет маршрута, нет координат, дубликат, цена не в евро) дашборд не показывает: они есть в Excel на листе «Все активные».
   - Скрытое объявление с другой причиной исключения (например, дороже бюджета) показывается, только если включены оба переключателя.
   - У главного объявления группы дубликатов в метках есть «ещё N объявлений этого участка».
   - Фильтры боковой панели действуют и на карту.
6. **Карточка.**
   - Открывается выбором строки в таблице, точки на карте или по ссылке `?ad=<id>` (карточка вверху страницы с кнопкой «Закрыть карточку»).
   - Фото загружает браузер прямо с сайта 999.md по ссылкам из базы. Программа сама на сайт не ходит.
   - «Рядом» показывает ближайший объект каждой категории не дальше `scoring.poi_zero_m` (1,5 км) с названием из OpenStreetMap (`name`, иначе `brand`).
   - Вклад компонента в балл считается по текущим весам из настроек.
7. **Отметки ★ и «скрыть».**
   - Пишутся под блокировкой прогона `data/run.lock`, без ожидания. Если идёт прогон, отметка не ставится, и страница просит повторить через минуту. Иначе пересчёт баллов посреди прогона мог бы затереть только что поставленное «скрыто».
   - «Скрыть» сразу пересчитывает места, как `/hide` в боте.
   - Ошибка SQLite «database is locked» считается той же занятостью.
   - Цена решения: если отметку поставили ровно в момент старта прогона по расписанию, этот прогон пропустит слот. Отметка длится миллисекунды, так что совпадение маловероятно.
8. **Сброс выбора.**
   - Ключ таблицы и карты включает фильтры и счётчик `generation`. Отметка увеличивает счётчик, поэтому после «скрыть» строка не исчезает из-под выбора, а выбор сбрасывается вместе с карточкой.
   - Ключи ползунков включают их границы, а ключ списка сёл — его состав: если после прогона они изменились, виджет создаётся заново.
9. **Рынок.**
   - Медиана €/сотку считается по неделям (понедельник — воскресенье по Кишинёву) за последние `dashboard.market_weeks` (12) недель.
   - В неделю входят объявления, которые были на сайте хотя бы раз за эту неделю (`first_seen_at` раньше конца недели, `last_seen_at` не раньше начала), с последней ценой до конца недели.
   - Учитываются все объявления, прошедшие фильтры типа и площади (`filter_reason IS NULL`), включая дороже бюджета (спека §2). Дубликаты и подозрительные не отбрасываются: это обзор рынка, а не медиана оценки.
   - Цены не в евро переводятся по последнему курсу BNM из кэша.
   - Село показывается при `n ≥ dashboard.market_min_n` (3). Строка «Все сёла» строится по всем объявлениям. Сёла с одинаковыми названиями различаются по району.
   - В таблице есть изменение медианы за `dashboard.market_compare_weeks` (4) недели. Данные копятся с 29.10.2026, поэтому сначала там будет «—».
10. **Безопасность вывода.**
    - Текст с сайта и из базы попадает в `st.markdown` / `st.subheader` / `st.caption` только через `ui.plain()`, который экранирует Markdown и `$`.
    - Описание объявления показывается через `st.text`.
    - Подсказки карты (HTML deck.gl) экранируются `html.escape`.
11. **Зависимости:** `streamlit>=1.66`, `pydeck>=0.9`, `pandas>=2.2`. pandas и так приходит со Streamlit, но дашборд импортирует его напрямую, поэтому зависимость объявлена явно. API проверены на Streamlit 1.66.1:
    - выбор строки `st.dataframe(on_select="rerun", selection_mode="single-row")`;
    - выбор точки `st.pydeck_chart(on_select="rerun", selection_mode="single-object")` → `{"indices": {...}, "objects": {layer_id: [данные точки]}}`;
    - `AppTest` с `query_params`, кнопками, флажками и ползунками.

## Global Constraints

- Python `>=3.12`, запуск только через `uv`. Новые зависимости: `streamlit>=1.66`, `pydeck>=0.9`, `pandas>=2.2`.
- Программа не делает новых запросов к 999.md и внешним сервисам. Дашборд только читает базу и пишет отметки в `user_marks`/`scores`.
- Показывается только то, что уже хранится в базе: телефонов и имён продавцов там нет. `feature(id: 16)` и `owner` не запрашиваются.
- Тексты для пользователя — на русском. Время на странице — по Кишинёву (`settings.schedule.zone`), в базе — ISO 8601 в UTC.
- Модули `dashboard/listings.py`, `dashboard/card.py`, `dashboard/market.py` и `homealize/dashboard/__init__.py` не импортируют Streamlit; `cli.py` импортирует Streamlit только внутри `_run_streamlit`.
- Функции `storage/*` не делают commit. Исключение — `card.set_mark`: он фиксирует отметку сам, как команды бота.
- **Тесты:**
  - не ходят в сеть;
  - не запускают настоящий сервер Streamlit (страница проверяется через `AppTest`, `cli._run_streamlit` подменяется);
  - не трогают `data/homealize.db`, `logs/` и порт 8501.
- Исполнители никогда не запускают `scripts\autostart.ps1 -Install` или `-Remove` и не запускают дашборд на настоящей базе: живую проверку на копии базы и другом порту делает контролёр.
- Пока идёт работа, у пользователя из этого же `.venv` работает serve. `uv add` и `uv sync` его не трогают. Если uv не может заменить занятый файл — остановиться и сообщить контролёру, а не останавливать процессы.
- Окончания строк — LF; `scripts/autostart.ps1` — UTF-8 с BOM (Windows PowerShell 5.1 без BOM читает кириллицу неверно).
- `code_artifact.md` в корне не добавлять в git.

## Структура файлов

```
pyproject.toml, uv.lock                     + streamlit, pydeck, pandas
config/settings.yaml                        + секция dashboard (host 0.0.0.0)
scripts/autostart.ps1                       вторая задача «Homealize Dashboard», адрес для телефона
src/homealize/config.py                     + DashboardSettings, Settings.dashboard
src/homealize/storage/scores.py             выборка load_scored/get_scored: + коммуникации, описание, settlement_id,
                                              site_updated_at
src/homealize/dashboard/__init__.py         НОВЫЙ: APP_PATH, CONFIG_ENV
src/homealize/dashboard/listings.py         НОВЫЙ: строки рейтинга, фильтры, метки, цвет, выбор на карте
src/homealize/dashboard/card.py             НОВЫЙ: карточка, ближайшие объекты, отметки под блокировкой
src/homealize/dashboard/market.py           НОВЫЙ: медианы по неделям, таблица рынка
src/homealize/dashboard/ui.py               НОВЫЙ: страница Streamlit
src/homealize/dashboard/app.py              НОВЫЙ: файл для `streamlit run`
src/homealize/cli.py                        + команда dashboard
tests/test_config.py, tests/storage/test_scores.py, tests/test_cli.py
tests/dashboard/__init__.py, test_listings.py, test_card.py, test_market.py, test_app.py   НОВЫЕ
README.md, спека §14
```

## Порядок задач

1. Настройки, зависимости, данные рейтинга (`listings.py`).
2. Карточка и отметки (`card.py`).
3. Рынок (`market.py`).
4. Страница Streamlit (`ui.py`, `app.py`).
5. Команда `dashboard`, автозапуск, README, спека §14.

Зависимости: задача 1 — первая. Задачи 2, 3 и 5 не пересекаются по файлам и могут идти параллельно после 1. Задача 4 — после 1–3 (с файлами задачи 5 она тоже не пересекается).

---

### Task 1: Настройки, зависимости, данные рейтинга

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (через `uv add`)
- Modify: `src/homealize/config.py`
- Modify: `config/settings.yaml`
- Modify: `src/homealize/storage/scores.py:29-33`
- Create: `src/homealize/dashboard/__init__.py`
- Create: `src/homealize/dashboard/listings.py`
- Create: `tests/dashboard/__init__.py` (пустой)
- Test: `tests/test_config.py`, `tests/storage/test_scores.py`, `tests/dashboard/test_listings.py`

**Interfaces:**
- Consumes (уже есть):
  - `storage.scores.load_scored(conn) -> list[sqlite3.Row]` — порядок: сначала места, потом остальные по баллу;
  - `storage.marks.marked(conn, mark) -> set[str]`, `marks.FAV`, `marks.HIDDEN`;
  - `config.UTILITIES = ("electricity", "gas", "water", "sewerage")`;
  - `notify.format.NO_SETTLEMENT = "село не указано"`, `notify.format.plural(n, one, few, many)`;
  - `scoring.components.EXCLUSION_LABELS`, `FLAG_LABELS`.
- Produces:
  - `homealize.config.DashboardSettings` с полями `host: str = "127.0.0.1"`, `port: int = 8501` (1024–65535), `log_path: Path = Path("logs/dashboard.log")`, `photos: int = 6`, `market_min_n: int = 3`, `market_weeks: int = 12`, `market_compare_weeks: int = 4`; `Settings.dashboard`.
  - Строки `load_scored` / `get_scored` дополнительно содержат `settlement_id`, `site_updated_at`, `description`, `has_electricity`, `has_gas`, `has_water`, `has_sewerage`.
  - `homealize.dashboard.APP_PATH: Path` (`…/dashboard/app.py`), `homealize.dashboard.CONFIG_ENV = "HOMEALIZE_CONFIG"`.
  - В `homealize.dashboard.listings`:
    - `ListingView` (frozen dataclass): `listing_id, rank, total, settlement, region, area_sot, price_eur, price_per_sot_eur, deviation_percent, route_min, lat, lon, utilities: frozenset[str], flags: tuple[str, ...], excluded_reason, fav, hidden, group_extra: int, url`;
    - `Filters` (frozen, хешируемый): `price, area: tuple[float, float] | None`, `max_minutes: float | None`, `utilities, settlements: frozenset[str]`, `only_favs, show_hidden, show_suspicious, show_over_budget: bool`;
    - `Bounds`: `price, area: tuple[float, float]`, `max_minutes: float`;
    - `load_listings(conn) -> list[ListingView]`;
    - `candidates(rows) -> list[ListingView]`;
    - `apply_filters(rows, filters) -> list[ListingView]`;
    - `bounds(rows) -> Bounds | None`;
    - `settlement_options(rows) -> list[str]`;
    - `labels(row) -> str`;
    - `score_color(total) -> tuple[int, int, int]`;
    - `picked_listing_id(selection, layer=LISTINGS_LAYER) -> str | None`;
    - константы `OPTIONAL_REASONS`, `LISTINGS_LAYER = "listings"`.

- [ ] **Step 1: Добавить зависимости**

Run: `uv add "streamlit>=1.66" "pydeck>=0.9" "pandas>=2.2"`
Expected: в `dependencies` файла `pyproject.toml` появились три строки, `uv.lock` обновлён, `uv run python -c "import streamlit, pydeck, pandas"` проходит.

- [ ] **Step 2: Написать падающие тесты настроек и выборки**

В `tests/test_config.py` заменить тест `test_project_settings_file_matches_defaults` и добавить два новых в конец файла:

```python
def test_project_settings_file_matches_defaults():
    # Единственное отличие от умолчаний — выбор пользователя: дашборд открывается с телефона в той же сети.
    expected = Settings.model_validate({"dashboard": {"host": "0.0.0.0"}})
    assert load_settings(PROJECT_ROOT / "config" / "settings.yaml") == expected
```

```python
def test_stage4_defaults_match_spec():
    d = Settings().dashboard
    assert (d.host, d.port, d.log_path) == ("127.0.0.1", 8501, Path("logs/dashboard.log"))
    assert (d.photos, d.market_min_n, d.market_weeks, d.market_compare_weeks) == (6, 3, 12, 4)


def test_dashboard_port_must_be_unprivileged():
    with pytest.raises(ValidationError):
        Settings.model_validate({"dashboard": {"port": 80}})
```

В конец `tests/storage/test_scores.py`:

```python
def test_scored_rows_carry_utilities_and_description(db):
    seed(db, score_record("a"))
    row = get_scored(db, "a")
    assert (row["has_electricity"], row["has_gas"], row["has_water"], row["has_sewerage"]) == (1, 1, 1, 0)
    assert row["description"] == "Ровный участок."
    assert row["site_updated_at"].startswith("2026-10-28")
    assert "settlement_id" in row.keys()
```

- [ ] **Step 3: Запустить — тесты падают**

Run: `uv run pytest tests/test_config.py tests/storage/test_scores.py -q`
Expected: FAIL — у `Settings` нет поля `dashboard`, в строке нет `has_electricity`.

- [ ] **Step 4: Настройки и выборка**

В `src/homealize/config.py` после класса `TelegramSettings` добавить:

```python
class DashboardSettings(_Section):
    host: str = Field("127.0.0.1", min_length=1)  # 0.0.0.0 — открыть и с других устройств в этой сети
    port: int = Field(8501, ge=1024, le=65535)
    log_path: Path = Path("logs/dashboard.log")  # вывод дашборда под pythonw (автозапуск)
    photos: int = Field(6, ge=0, le=20)  # сколько фото показывать в карточке
    market_min_n: int = Field(3, ge=1)  # медиана села на вкладке «Рынок» — если объявлений не меньше
    market_weeks: int = Field(12, ge=1, le=104)  # сколько недель на графике рынка
    market_compare_weeks: int = Field(4, ge=1)  # изменение медианы за столько недель
```

и в класс `Settings` последним полем:

```python
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
```

В конец `config/settings.yaml`:

```yaml

dashboard:
  host: "0.0.0.0"                # открыть и с телефона в той же Wi-Fi; "127.0.0.1" — только с этого ПК
  port: 8501                     # адрес на этом ПК: http://localhost:8501
  log_path: "logs/dashboard.log" # вывод дашборда при автозапуске
  photos: 6                      # сколько фото показывать в карточке участка
  market_min_n: 3                # «Рынок»: медиана села — если объявлений не меньше
  market_weeks: 12               # «Рынок»: сколько недель на графике
  market_compare_weeks: 4        # «Рынок»: изменение медианы за столько недель
```

В `src/homealize/storage/scores.py` заменить `_SCORED`:

```python
_SCORED = (
    "SELECT s.*, l.title, l.url, l.region, l.settlement, l.settlement_id, l.zone, l.area_sot, l.price_original, "
    "l.currency, l.author_type, l.first_seen_at, l.site_updated_at, l.images, l.description, "
    "l.has_electricity, l.has_gas, l.has_water, l.has_sewerage "
    "FROM scores s JOIN listings l ON l.id = s.listing_id"
)
```

- [ ] **Step 5: Запустить — тесты проходят**

Run: `uv run pytest tests/test_config.py tests/storage/test_scores.py -q`
Expected: PASS.

- [ ] **Step 6: Написать падающие тесты `listings`**

Создать пустой `tests/dashboard/__init__.py` и `tests/dashboard/test_listings.py`:

```python
from datetime import UTC, datetime

from homealize.dashboard.listings import (
    Filters,
    apply_filters,
    bounds,
    candidates,
    labels,
    load_listings,
    picked_listing_id,
    score_color,
    settlement_options,
)
from homealize.storage import marks, repo
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def seed(db, *items, favs=(), hidden=()):
    """items — пары (поля объявления, поля оценки); id берётся из полей объявления."""
    records = []
    for listing, score in items:
        repo.upsert_listing(db, make_listing(**listing), status="active", filter_reason=None, now=T0)
        records.append(score_record(listing["id"], **score))
    replace_scores(db, records)
    for listing_id in favs:
        marks.add_mark(db, listing_id, marks.FAV, now=T0)
    for listing_id in hidden:
        marks.add_mark(db, listing_id, marks.HIDDEN, now=T0)
    db.commit()


def ids(rows):
    return [row.listing_id for row in rows]


def test_load_listings_builds_views(db):
    seed(
        db,
        ({"id": "a"}, {"total": 80.0, "rank": 1}),
        ({"id": "b", "settlement": None}, {"total": 90.0, "rank": None, "excluded_reason": "over_budget"}),
        favs=["a"],
    )
    a, b = load_listings(db)
    assert (a.listing_id, a.rank, a.total, a.fav, a.hidden) == ("a", 1, 80.0, True, False)
    assert (a.settlement, a.region, a.area_sot) == ("Гратиешты", "Кишинёв мун.", 6.0)
    assert a.deviation_percent == -17  # 4666,67 €/сот. при медиане 5600
    assert a.utilities == frozenset({"electricity", "gas", "water"})
    assert a.flags == ("no_sewerage",)
    assert (a.route_min, a.lat, a.lon, a.group_extra) == (10.0, 47.09921, 28.8165, 0)
    assert a.url == "https://999.md/ru/100000001"
    assert (b.settlement, b.excluded_reason, b.fav) == ("село не указано", "over_budget", False)


def test_group_extra_counts_other_members_of_the_group(db):
    duplicate = {"duplicate_group": "a", "is_group_primary": False, "rank": None, "excluded_reason": "duplicate"}
    seed(
        db,
        ({"id": "a"}, {"duplicate_group": "a", "rank": 1}),
        ({"id": "b"}, duplicate),
        ({"id": "c"}, duplicate),
        ({"id": "d"}, {"rank": 2}),
    )
    views = {row.listing_id: row for row in load_listings(db)}
    assert (views["a"].group_extra, views["d"].group_extra) == (2, 0)
    assert labels(views["a"]) == "ещё 2 объявления этого участка; ⚠ нет канализации (не указано в объявлении)"


def test_labels_name_the_exclusion_reason_first(db):
    seed(db, ({"id": "a"}, {"rank": None, "excluded_reason": "over_budget", "flags": '["suspicious_price"]'}))
    (row,) = load_listings(db)
    assert labels(row) == "дороже бюджета; 🔍 подозрительно дёшево — проверить вручную"


def test_default_filters_show_only_the_ranking(db):
    seed(
        db,
        ({"id": "a"}, {"total": 80.0, "rank": 1}),
        ({"id": "b"}, {"total": 95.0, "rank": None, "excluded_reason": "over_budget"}),
        ({"id": "c"}, {"total": 60.0, "rank": None, "excluded_reason": "suspicious_price"}),
        ({"id": "d"}, {"total": 75.0, "rank": None, "excluded_reason": "hidden"}),
        ({"id": "e"}, {"total": 99.0, "rank": None, "excluded_reason": "too_far"}),
        hidden=["d"],
    )
    rows = load_listings(db)
    assert ids(apply_filters(rows, Filters())) == ["a"]
    everything = Filters(show_hidden=True, show_suspicious=True, show_over_budget=True)
    assert ids(apply_filters(rows, everything)) == ["a", "b", "d", "c"]  # сначала места, потом по баллу
    assert ids(candidates(rows)) == ["a", "b", "d", "c"]  # «слишком далеко» дашборд не показывает


def test_hidden_listing_with_another_reason_needs_both_switches(db):
    seed(db, ({"id": "a"}, {"rank": None, "excluded_reason": "over_budget"}), hidden=["a"])
    rows = load_listings(db)
    assert apply_filters(rows, Filters(show_over_budget=True)) == []
    assert ids(apply_filters(rows, Filters(show_over_budget=True, show_hidden=True))) == ["a"]


def test_numeric_and_set_filters(db):
    seed(
        db,
        ({"id": "a", "area_sot": 6.0, "settlement": "Сынжера"}, {"rank": 1, "price_eur": 28000.0, "route_min": 10.0}),
        (
            {"id": "b", "area_sot": 8.0, "settlement": "Ватра", "has_sewerage": True},
            {"rank": 2, "price_eur": 40000.0, "route_min": 20.0},
        ),
        favs=["b"],
    )
    rows = load_listings(db)
    assert ids(apply_filters(rows, Filters(price=(28000, 30000)))) == ["a"]  # границы включительно
    assert ids(apply_filters(rows, Filters(area=(7.0, 10.0)))) == ["b"]
    assert ids(apply_filters(rows, Filters(max_minutes=10.0))) == ["a"]
    assert ids(apply_filters(rows, Filters(utilities=frozenset({"sewerage"})))) == ["b"]
    assert ids(apply_filters(rows, Filters(settlements=frozenset({"Сынжера"})))) == ["a"]
    assert ids(apply_filters(rows, Filters(only_favs=True))) == ["b"]


def test_bounds_and_settlement_options_cover_every_showable_row(db):
    seed(
        db,
        ({"id": "a", "area_sot": 5.3, "settlement": "Сынжера"}, {"rank": 1, "price_eur": 28100.0, "route_min": 9.2}),
        (
            {"id": "b", "area_sot": 9.6, "settlement": "Бачой"},
            {"rank": None, "excluded_reason": "over_budget", "price_eur": 61900.0, "route_min": 21.5},
        ),
        ({"id": "c", "settlement": "Ватра"}, {"rank": None, "excluded_reason": "too_far", "price_eur": 1000.0}),
    )
    rows = load_listings(db)
    limits = bounds(rows)
    assert limits.price == (28100, 61900)
    assert limits.area == (5.0, 10.0)
    assert limits.max_minutes == 22.0
    assert settlement_options(rows) == ["Бачой", "Сынжера"]
    assert bounds([]) is None


def test_score_color_runs_red_yellow_green():
    assert score_color(0) == (0xF8, 0x69, 0x6B)
    assert score_color(50) == (0xFF, 0xEB, 0x84)
    assert score_color(100) == (0x63, 0xBE, 0x7B)
    assert score_color(25) == (252, 170, 120)
    assert score_color(-5) == score_color(0)
    assert score_color(150) == score_color(100)


def test_picked_listing_id_reads_the_map_selection():
    selection = {"indices": {"listings": [0]}, "objects": {"listings": [{"id": "a", "lat": 47.0}]}}
    assert picked_listing_id(selection) == "a"
    assert picked_listing_id({"indices": {}, "objects": {"entries": [{"title": "Въезд"}]}}) is None
    assert picked_listing_id({"indices": {}, "objects": {}}) is None
    assert picked_listing_id(None) is None
```

- [ ] **Step 7: Запустить — тесты падают**

Run: `uv run pytest tests/dashboard/test_listings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.dashboard'`.

- [ ] **Step 8: Пакет `dashboard` и `listings.py`**

`src/homealize/dashboard/__init__.py`:

```python
"""Веб-дашборд на Streamlit (спека §7.2): `uv run homealize dashboard`."""

from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")  # файл, который исполняет `streamlit run`
CONFIG_ENV = "HOMEALIZE_CONFIG"  # путь к settings.yaml для страницы дашборда
```

`src/homealize/dashboard/listings.py`:

```python
"""Данные вкладок «Топ» и «Карта» дашборда (спека §7.2): строки рейтинга, фильтры, метки, цвет балла.

Модуль не импортирует Streamlit: здесь только чтение базы и чистые функции.
"""

import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from homealize.config import UTILITIES
from homealize.notify.format import NO_SETTLEMENT, plural
from homealize.scoring.components import EXCLUSION_LABELS, FLAG_LABELS
from homealize.storage import marks
from homealize.storage.scores import load_scored

# Причины исключения, которые можно показать переключателями. Остальные (далеко, нет маршрута или координат,
# дубликат, цена не в евро) дашборд не показывает: они есть в Excel на листе «Все активные».
OPTIONAL_REASONS = frozenset({"hidden", "suspicious_price", "over_budget"})
LISTINGS_LAYER = "listings"  # id слоя участков на карте: по нему находится выбранная точка
# Цвет балла как в Excel: 0 — красный, 50 — жёлтый, 100 — зелёный.
_SCALE = ((0.0, (0xF8, 0x69, 0x6B)), (50.0, (0xFF, 0xEB, 0x84)), (100.0, (0x63, 0xBE, 0x7B)))


@dataclass(frozen=True)
class ListingView:
    listing_id: str
    rank: int | None
    total: float
    settlement: str  # NO_SETTLEMENT, если село не указано
    region: str | None
    area_sot: float | None
    price_eur: float | None
    price_per_sot_eur: float | None
    deviation_percent: int | None  # к медиане рынка: −17 — на 17% дешевле
    route_min: float | None
    lat: float | None
    lon: float | None
    utilities: frozenset[str]  # из UTILITIES: что указано в объявлении
    flags: tuple[str, ...]
    excluded_reason: str | None
    fav: bool
    hidden: bool
    group_extra: int  # сколько ещё объявлений в той же группе дубликатов
    url: str


@dataclass(frozen=True)
class Filters:
    """Фильтры боковой панели. None и пустые множества — без ограничения."""

    price: tuple[float, float] | None = None  # €, включительно
    area: tuple[float, float] | None = None  # сотки, включительно
    max_minutes: float | None = None
    utilities: frozenset[str] = frozenset()  # обязательные коммуникации
    settlements: frozenset[str] = frozenset()
    only_favs: bool = False
    show_hidden: bool = False
    show_suspicious: bool = False
    show_over_budget: bool = False


@dataclass(frozen=True)
class Bounds:
    """Границы ползунков: по всем строкам, которые можно показать хоть каким-то переключателем."""

    price: tuple[float, float]
    area: tuple[float, float]
    max_minutes: float


def load_listings(conn: sqlite3.Connection) -> list[ListingView]:
    """Все строки снимка оценки в порядке рейтинга: сначала места, потом остальные по баллу."""
    rows = load_scored(conn)
    favs = marks.marked(conn, marks.FAV)
    hidden = marks.marked(conn, marks.HIDDEN)
    group_sizes = Counter(row["duplicate_group"] for row in rows if row["duplicate_group"] is not None)
    return [_view(row, favs, hidden, group_sizes) for row in rows]


def candidates(rows: Iterable[ListingView]) -> list[ListingView]:
    """Строки, которые дашборд может показать: в рейтинге или исключённые по причинам из OPTIONAL_REASONS."""
    return [row for row in rows if row.excluded_reason is None or row.excluded_reason in OPTIONAL_REASONS]


def apply_filters(rows: Iterable[ListingView], filters: Filters) -> list[ListingView]:
    """Строки под фильтры; порядок сохраняется."""
    allowed: set[str | None] = {None}
    if filters.show_hidden:
        allowed.add("hidden")
    if filters.show_suspicious:
        allowed.add("suspicious_price")
    if filters.show_over_budget:
        allowed.add("over_budget")
    return [row for row in rows if row.excluded_reason in allowed and _passes(row, filters)]


def bounds(rows: Iterable[ListingView]) -> Bounds | None:
    """Границы ползунков по candidates(rows); None — показывать нечего."""
    shown = candidates(rows)
    prices = [row.price_eur for row in shown if row.price_eur is not None]
    areas = [row.area_sot for row in shown if row.area_sot is not None]
    minutes = [row.route_min for row in shown if row.route_min is not None]
    if not prices or not areas or not minutes:
        return None
    return Bounds(
        price=(math.floor(min(prices)), math.ceil(max(prices))),
        area=(math.floor(min(areas) * 2) / 2, math.ceil(max(areas) * 2) / 2),  # с шагом 0,5 сотки
        max_minutes=float(math.ceil(max(minutes))),
    )


def settlement_options(rows: Iterable[ListingView]) -> list[str]:
    """Сёла для фильтра: по всем строкам, которые можно показать, по алфавиту."""
    return sorted({row.settlement for row in candidates(rows)}, key=str.casefold)


def labels(row: ListingView) -> str:
    """Колонка «Метки»: причина исключения, другие объявления того же участка, метки оценки."""
    parts = []
    if row.excluded_reason:
        parts.append(EXCLUSION_LABELS.get(row.excluded_reason, row.excluded_reason))
    if row.group_extra:
        word = plural(row.group_extra, "объявление", "объявления", "объявлений")
        parts.append(f"ещё {row.group_extra} {word} этого участка")
    parts += [FLAG_LABELS.get(flag, flag) for flag in row.flags]
    return "; ".join(parts)


def score_color(total: float) -> tuple[int, int, int]:
    """Цвет точки на карте по баллу: та же шкала, что в Excel."""
    value = min(100.0, max(0.0, total))
    for (low, low_rgb), (high, high_rgb) in zip(_SCALE, _SCALE[1:]):
        if value <= high:
            t = (value - low) / (high - low)
            red, green, blue = (round(a + (b - a) * t) for a, b in zip(low_rgb, high_rgb))
            return red, green, blue
    return _SCALE[-1][1]


def picked_listing_id(selection: Mapping | None, layer: str = LISTINGS_LAYER) -> str | None:
    """id участка, выбранного на карте (выбор st.pydeck_chart: {"objects": {слой: [данные точки]}})."""
    if not selection:
        return None
    picked = (selection.get("objects") or {}).get(layer) or []
    return str(picked[0]["id"]) if picked and "id" in picked[0] else None


def _passes(row: ListingView, filters: Filters) -> bool:
    if row.hidden and not filters.show_hidden:
        return False  # скрытое «дороже бюджета» тоже прячется
    if filters.only_favs and not row.fav:
        return False
    if filters.price is not None and not _within(row.price_eur, filters.price):
        return False
    if filters.area is not None and not _within(row.area_sot, filters.area):
        return False
    if filters.max_minutes is not None and (row.route_min is None or row.route_min > filters.max_minutes):
        return False
    if not filters.utilities <= row.utilities:
        return False
    return not filters.settlements or row.settlement in filters.settlements


def _within(value: float | None, limits: tuple[float, float]) -> bool:
    return value is not None and limits[0] <= value <= limits[1]


def _view(row: sqlite3.Row, favs: set[str], hidden: set[str], group_sizes: Counter) -> ListingView:
    price_per_sot, market = row["price_per_sot_eur"], row["market_median_eur_per_sot"]
    group = row["duplicate_group"]
    return ListingView(
        listing_id=row["listing_id"],
        rank=row["rank"],
        total=row["total"],
        settlement=row["settlement"] or NO_SETTLEMENT,
        region=row["region"],
        area_sot=row["area_sot"],
        price_eur=row["price_eur"],
        price_per_sot_eur=price_per_sot,
        deviation_percent=round((price_per_sot / market - 1) * 100) if price_per_sot and market else None,
        route_min=row["route_min"],
        lat=row["lat"],
        lon=row["lon"],
        utilities=frozenset(name for name in UTILITIES if row[f"has_{name}"]),
        flags=tuple(json.loads(row["flags"])),
        excluded_reason=row["excluded_reason"],
        fav=row["listing_id"] in favs,
        hidden=row["listing_id"] in hidden,
        group_extra=group_sizes[group] - 1 if group is not None else 0,
        url=row["url"],
    )
```

- [ ] **Step 9: Запустить все тесты**

Run: `uv run pytest -q`
Expected: PASS (468 прежних + 12 новых).

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock config/settings.yaml src/homealize/config.py src/homealize/storage/scores.py src/homealize/dashboard tests/test_config.py tests/storage/test_scores.py tests/dashboard
git commit -m "feat: dashboard settings, dependencies and ranking data"
```

---

### Task 2: Карточка и отметки

**Files:**
- Create: `src/homealize/dashboard/card.py`
- Test: `tests/dashboard/test_card.py`

**Interfaces:**
- Consumes:
  - `storage.scores.get_scored(conn, listing_id) -> sqlite3.Row | None` (с полями задачи 1), `apply_hidden(conn, hidden: set[str])`;
  - `storage.repo.get_price_history(conn, listing_id) -> list[sqlite3.Row]` (по времени);
  - `storage.poi_cache.load_poi(conn) -> list[models.Poi]` (`key, category, name, brand, lat, lon`);
  - `storage.marks.add_mark(conn, id, mark, *, now) -> bool`, `remove_mark(conn, id, mark) -> bool`, `marked`;
  - `runlock.hold_lock(path)`, `runlock.LockBusyError`;
  - `geo.haversine_m(lat1, lon1, lat2, lon2) -> float`;
  - `scoring.components.CATEGORY_LABELS`, `config.POI_CATEGORIES`;
  - `Settings.dashboard.photos`, `Settings.scoring.weights`, `Settings.scoring.poi_zero_m`, `Settings.storage.run_lock_path`.
- Produces (`homealize.dashboard.card`):
  - `COMPONENT_LABELS: dict[str, str]` — `price` → «Цена», `route` → «Дорога», `infrastructure` → «Инфраструктура», `utilities` → «Коммуникации», `settlement` → «Село»;
  - `ComponentLine(name, label, score, weight, points, note)`;
  - `PricePoint(observed_at: datetime, price: float, currency: str | None)`;
  - `NearbyPoi(category, label, name: str | None, distance_m: int)`;
  - `Card(row, fav, hidden, components, history, nearby, photos)`;
  - `load_card(conn, settings, listing_id) -> Card | None`;
  - `component_lines(row, settings) -> list[ComponentLine]`;
  - `price_points(conn, listing_id) -> list[PricePoint]`;
  - `nearby_pois(conn, lat, lon, *, max_m) -> list[NearbyPoi]`;
  - `class MarkBusyError(Exception)`;
  - `set_mark(conn, settings, listing_id, mark, *, on: bool, now: datetime) -> bool` — фиксирует сам.

- [ ] **Step 1: Написать падающие тесты**

`tests/dashboard/test_card.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from homealize.config import Settings
from homealize.dashboard.card import MarkBusyError, load_card, nearby_pois, set_mark
from homealize.models import Poi
from homealize.runlock import hold_lock
from homealize.storage import marks, poi_cache, repo
from homealize.storage.db import connect, migrate
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
LAT, LON = 47.09921, 28.8165  # точка make_listing


@pytest.fixture
def settings(tmp_path):
    return Settings.model_validate({"storage": {"run_lock_path": str(tmp_path / "run.lock")}})


def seed(conn, *records, images=None):
    extra = {"images": images} if images is not None else {}
    for record in records:
        repo.upsert_listing(
            conn, make_listing(id=record["listing_id"], **extra), status="active", filter_reason=None, now=T0
        )
    replace_scores(conn, list(records))
    conn.commit()


def test_card_holds_breakdown_marks_and_photos(db, settings):
    photos = tuple(f"https://i.simpalsmedia.com/999.md/BoardImages/900x900/{n}.jpg" for n in range(8))
    seed(db, score_record("a"), images=photos)
    marks.add_mark(db, "a", marks.FAV, now=T0)
    card = load_card(db, settings, "a")
    assert (card.fav, card.hidden) == (True, False)
    assert card.photos == list(photos[:6])
    assert card.row["settlement"] == "Гратиешты"
    assert [line.name for line in card.components] == ["price", "route", "infrastructure", "utilities", "settlement"]
    price = card.components[0]
    assert (price.label, price.score, price.weight, price.points) == ("Цена", 66.7, 0.30, 20.0)
    assert price.note == "Цена 67: на 17% дешевле медианы"
    assert card.components[2].note == ""  # пояснения к инфраструктуре в этой оценке нет


def test_card_is_none_for_listing_outside_the_scores(db, settings):
    assert load_card(db, settings, "missing") is None


def test_card_history_follows_price_changes(db, settings):
    seed(db, score_record("a"))
    later = T0 + timedelta(days=1)
    repo.upsert_listing(db, make_listing(id="a", price_original=26000.0), status="active", filter_reason=None, now=later)
    db.commit()
    history = load_card(db, settings, "a").history
    assert [(point.price, point.currency) for point in history] == [(28000.0, "EUR"), (26000.0, "EUR")]
    assert history[1].observed_at == later


def test_nearby_pois_name_the_nearest_object_of_each_category(db):
    poi_cache.replace_poi(
        db,
        [
            Poi(key="node/1", category="supermarket", name="Linella", brand=None, lat=LAT + 0.0009, lon=LON),  # ≈100 м
            Poi(key="node/2", category="supermarket", name="Fidesco", brand=None, lat=LAT + 0.0027, lon=LON),  # ≈300 м
            Poi(key="node/3", category="pharmacy", name=None, brand="Felicia", lat=LAT, lon=LON + 0.004),  # ≈300 м
            Poi(key="node/4", category="school", name="Лицей", brand=None, lat=LAT + 0.02, lon=LON),  # ≈2,2 км
        ],
        now=T0,
    )
    nearby = nearby_pois(db, LAT, LON, max_m=1500)
    assert [(poi.category, poi.label, poi.name) for poi in nearby] == [
        ("supermarket", "супермаркет", "Linella"),
        ("pharmacy", "аптека", "Felicia"),
    ]
    assert 95 <= nearby[0].distance_m <= 105
    assert nearby_pois(db, None, None, max_m=1500) == []


def test_set_mark_hides_and_restores_with_new_ranks(db, settings):
    seed(db, score_record("a", total=80.0, rank=1), score_record("b", total=70.0, rank=2))
    assert set_mark(db, settings, "a", marks.HIDDEN, on=True, now=T0) is True
    assert set_mark(db, settings, "a", marks.HIDDEN, on=True, now=T0) is False
    rows = {row["listing_id"]: row for row in db.execute("SELECT * FROM scores")}
    assert (rows["a"]["excluded_reason"], rows["a"]["rank"], rows["b"]["rank"]) == ("hidden", None, 1)
    assert set_mark(db, settings, "a", marks.HIDDEN, on=False, now=T0) is True
    assert {row["listing_id"]: row["rank"] for row in db.execute("SELECT * FROM scores")} == {"a": 1, "b": 2}
    assert load_card(db, settings, "a").hidden is False


def test_set_mark_commits_the_fav(tmp_path, settings):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    seed(conn, score_record("a"))
    assert set_mark(conn, settings, "a", marks.FAV, on=True, now=T0) is True
    other = connect(tmp_path / "test.db")
    try:
        assert marks.marked(other, marks.FAV) == {"a"}
    finally:
        other.close()
        conn.close()


def test_set_mark_refuses_while_a_run_holds_the_lock(db, settings):
    seed(db, score_record("a"))
    with hold_lock(settings.storage.run_lock_path), pytest.raises(MarkBusyError):
        set_mark(db, settings, "a", marks.FAV, on=True, now=T0)
    assert marks.marked(db, marks.FAV) == set()


def test_set_mark_rejects_unknown_marks(db, settings):
    seed(db, score_record("a"))
    with pytest.raises(ValueError):
        set_mark(db, settings, "a", "pinned", on=True, now=T0)
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/dashboard/test_card.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.dashboard.card'`.

- [ ] **Step 3: Реализация**

`src/homealize/dashboard/card.py`:

```python
"""Карточка участка в дашборде (спека §7.2) и отметки ★ / «скрыть».

Модуль не импортирует Streamlit. set_mark — единственная функция дашборда, которая пишет в базу.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from homealize.config import POI_CATEGORIES, Settings
from homealize.geo import haversine_m
from homealize.models import Poi
from homealize.runlock import LockBusyError, hold_lock
from homealize.scoring.components import CATEGORY_LABELS
from homealize.storage import marks, poi_cache, repo
from homealize.storage.scores import apply_hidden, get_scored

COMPONENT_LABELS = {
    "price": "Цена",
    "route": "Дорога",
    "infrastructure": "Инфраструктура",
    "utilities": "Коммуникации",
    "settlement": "Село",
}


class MarkBusyError(Exception):
    """Идёт прогон или база занята: отметка не поставлена, её нужно повторить через минуту."""


@dataclass(frozen=True)
class ComponentLine:
    name: str
    label: str
    score: float
    weight: float
    points: float  # вклад в итоговый балл: score × weight
    note: str  # пояснение из оценки


@dataclass(frozen=True)
class PricePoint:
    observed_at: datetime
    price: float
    currency: str | None


@dataclass(frozen=True)
class NearbyPoi:
    category: str
    label: str  # «супермаркет»
    name: str | None
    distance_m: int


@dataclass(frozen=True)
class Card:
    row: sqlite3.Row  # строка scores.get_scored
    fav: bool
    hidden: bool
    components: list[ComponentLine]
    history: list[PricePoint]
    nearby: list[NearbyPoi]
    photos: list[str]


def load_card(conn: sqlite3.Connection, settings: Settings, listing_id: str) -> Card | None:
    """Всё для карточки; None — объявления нет в снимке оценки."""
    row = get_scored(conn, listing_id)
    if row is None:
        return None
    return Card(
        row=row,
        fav=listing_id in marks.marked(conn, marks.FAV),
        hidden=listing_id in marks.marked(conn, marks.HIDDEN),
        components=component_lines(row, settings),
        history=price_points(conn, listing_id),
        nearby=nearby_pois(conn, row["lat"], row["lon"], max_m=settings.scoring.poi_zero_m),
        photos=json.loads(row["images"] or "[]")[: settings.dashboard.photos],
    )


def component_lines(row: sqlite3.Row, settings: Settings) -> list[ComponentLine]:
    """Разбивка балла по компонентам с весами из настроек."""
    scores = json.loads(row["components"])
    notes = json.loads(row["explanations"])
    weights = settings.scoring.weights
    lines = []
    for name, label in COMPONENT_LABELS.items():
        if name not in scores:
            continue
        weight = getattr(weights, name)
        lines.append(
            ComponentLine(name, label, scores[name], weight, round(scores[name] * weight, 1), notes.get(name, ""))
        )
    return lines


def price_points(conn: sqlite3.Connection, listing_id: str) -> list[PricePoint]:
    return [
        PricePoint(datetime.fromisoformat(entry["observed_at"]), entry["price_original"], entry["currency"])
        for entry in repo.get_price_history(conn, listing_id)
        if entry["price_original"] is not None
    ]


def nearby_pois(conn: sqlite3.Connection, lat: float | None, lon: float | None, *, max_m: float) -> list[NearbyPoi]:
    """Ближайший объект каждой категории не дальше max_m, в порядке POI_CATEGORIES."""
    if lat is None or lon is None:
        return []
    best: dict[str, tuple[float, Poi]] = {}
    for poi in poi_cache.load_poi(conn):
        distance = haversine_m(lat, lon, poi.lat, poi.lon)
        if distance <= max_m and (poi.category not in best or distance < best[poi.category][0]):
            best[poi.category] = (distance, poi)
    nearby = []
    for category in POI_CATEGORIES:
        if category in best:
            distance, poi = best[category]
            label = CATEGORY_LABELS.get(category, category)
            nearby.append(NearbyPoi(category, label, poi.name or poi.brand, round(distance)))
    return nearby


def set_mark(
    conn: sqlite3.Connection, settings: Settings, listing_id: str, mark: str, *, on: bool, now: datetime
) -> bool:
    """Ставит (on=True) или снимает ★ / «скрыть»; «скрыть» сразу меняет места, как /hide в боте.

    Пишет под блокировкой прогона: пересчёт баллов посреди прогона мог бы затереть отметку.
    True — отметка изменилась. MarkBusyError — идёт прогон или база занята.
    """
    if mark not in (marks.FAV, marks.HIDDEN):
        raise ValueError(f"Неизвестная отметка: {mark}")
    try:
        with hold_lock(settings.storage.run_lock_path):
            try:
                if on:
                    changed = marks.add_mark(conn, listing_id, mark, now=now)
                else:
                    changed = marks.remove_mark(conn, listing_id, mark)
                if mark == marks.HIDDEN:
                    apply_hidden(conn, marks.marked(conn, marks.HIDDEN))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except LockBusyError:
        raise MarkBusyError("идёт прогон") from None
    except sqlite3.OperationalError as exc:  # «database is locked»: в базу пишет другой процесс
        raise MarkBusyError(str(exc)) from None
    return changed
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `uv run pytest tests/dashboard/test_card.py -q`
Expected: PASS.

- [ ] **Step 5: Все тесты**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/dashboard/card.py tests/dashboard/test_card.py
git commit -m "feat: dashboard listing card and marks under the run lock"
```

---

### Task 3: Рынок

**Files:**
- Create: `src/homealize/dashboard/market.py`
- Test: `tests/dashboard/test_market.py`

**Interfaces:**
- Consumes:
  - таблицы `listings` (`id, settlement, region, area_sot, first_seen_at, last_seen_at, filter_reason`) и `price_history` (`listing_id, price_original, currency, observed_at`);
  - `enrich.currency.to_eur(price, currency, rates) -> float | None`;
  - время в базе — ISO 8601 UTC (`+00:00`), сравнивается как текст.
- Produces (`homealize.dashboard.market`):
  - `ALL_PLACES = "Все сёла"`;
  - `Place(settlement: str, region: str | None)` со свойством `label` («Сынжера (Новые Анены)»);
  - `WeeklyMedian(week: date, place: Place, n: int, median: float)`;
  - `MarketRow(place: Place, n: int, median: float, change_percent: float | None)`;
  - `week_start(moment: datetime, zone: ZoneInfo) -> date` — понедельник по местному времени;
  - `weekly_medians(conn, *, zone, rates, now, weeks, min_n) -> list[WeeklyMedian]` — недели по возрастанию, внутри недели по `place.label`;
  - `market_table(weekly, *, current: date, compare_weeks: int) -> list[MarketRow]` — «Все сёла» первой строкой, дальше по убыванию `n`.

- [ ] **Step 1: Написать падающие тесты**

`tests/dashboard/test_market.py`:

```python
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from homealize.dashboard.market import ALL_PLACES, Place, WeeklyMedian, market_table, week_start, weekly_medians
from homealize.storage import repo
from tests.factories import make_listing

ZONE = ZoneInfo("Europe/Chisinau")
MONDAY = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # понедельник, 11:00 по Кишинёву
THIS_WEEK, LAST_WEEK = date(2026, 11, 2), date(2026, 10, 26)
SINGERA = Place("Сынжера", "Новые Анены")
EVERYWHERE = Place(ALL_PLACES, None)


def add(db, listing_id, *, at, price=30000.0, settlement="Сынжера", region="Новые Анены", currency="EUR",
        filter_reason=None):
    listing = make_listing(
        id=listing_id, price_original=price, area_sot=6.0, settlement=settlement, region=region, currency=currency
    )
    status = "active" if filter_reason is None else "filtered_out"
    repo.upsert_listing(db, listing, status=status, filter_reason=filter_reason, now=at)
    db.commit()


def medians(db, *, rates=None, weeks=2, min_n=3):
    found = weekly_medians(db, zone=ZONE, rates=rates, now=MONDAY, weeks=weeks, min_n=min_n)
    return {(item.week, item.place): (item.n, item.median) for item in found}


def test_week_starts_on_local_monday():
    assert week_start(datetime(2026, 11, 1, 22, 30, tzinfo=UTC), ZONE) == THIS_WEEK  # 00:30 понедельника
    assert week_start(datetime(2026, 11, 1, 21, 30, tzinfo=UTC), ZONE) == LAST_WEEK  # 23:30 воскресенья


def test_median_per_settlement_and_for_all_places(db):
    for n, price in enumerate((30000.0, 36000.0, 42000.0)):
        add(db, f"s{n}", at=MONDAY, price=price)
    add(db, "v1", at=MONDAY, price=12000.0, settlement="Ватра", region="Кишинёв мун.")  # одно — село не показано
    assert medians(db, weeks=1) == {
        (THIS_WEEK, SINGERA): (3, 6000.0),
        (THIS_WEEK, EVERYWHERE): (4, 5500.0),
    }


def test_listing_counts_only_in_weeks_it_was_on_the_site(db):
    for n in range(3):
        add(db, f"old{n}", at=MONDAY - timedelta(weeks=1))
    result = medians(db)
    assert (LAST_WEEK, SINGERA) in result
    assert (THIS_WEEK, SINGERA) not in result


def test_week_uses_the_last_price_before_its_end(db):
    for n in range(3):
        add(db, f"s{n}", at=MONDAY - timedelta(weeks=1), price=30000.0)
        add(db, f"s{n}", at=MONDAY, price=24000.0)
    result = medians(db)
    assert result[(LAST_WEEK, SINGERA)] == (3, 5000.0)
    assert result[(THIS_WEEK, SINGERA)] == (3, 4000.0)


def test_other_currencies_need_a_rate(db):
    for n in range(3):
        add(db, f"m{n}", at=MONDAY, price=600000.0, currency="MDL")
    assert medians(db, weeks=1) == {}
    assert medians(db, weeks=1, rates={"MDL": 1.0, "EUR": 20.0})[(THIS_WEEK, SINGERA)] == (3, 5000.0)


def test_filtered_out_listings_are_ignored(db):
    for n in range(3):
        add(db, f"f{n}", at=MONDAY, filter_reason="area")
    assert medians(db, weeks=1) == {}


def test_market_table_orders_rows_and_compares_with_an_earlier_week():
    vatra = Place("Ватра", "Кишинёв мун.")
    weekly = [
        WeeklyMedian(date(2026, 10, 5), SINGERA, 4, 5000.0),
        WeeklyMedian(THIS_WEEK, vatra, 3, 7000.0),
        WeeklyMedian(THIS_WEEK, SINGERA, 5, 5500.0),
        WeeklyMedian(THIS_WEEK, EVERYWHERE, 8, 6000.0),
    ]
    rows = market_table(weekly, current=THIS_WEEK, compare_weeks=4)
    assert [(row.place.label, row.n, row.median, row.change_percent) for row in rows] == [
        ("Все сёла", 8, 6000.0, None),
        ("Сынжера (Новые Анены)", 5, 5500.0, 10.0),
        ("Ватра (Кишинёв мун.)", 3, 7000.0, None),
    ]
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/dashboard/test_market.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.dashboard.market'`.

- [ ] **Step 3: Реализация**

`src/homealize/dashboard/market.py`:

```python
"""Вкладка «Рынок» дашборда (спека §7.2): медиана €/сотку по сёлам и её динамика по неделям.

Медиана недели считается по объявлениям, которые были на сайте хотя бы раз за эту неделю, с их последней ценой
до конца недели. Учитываются все объявления, прошедшие фильтры типа и площади, включая дороже бюджета (спека §2).
Цены не в евро переводятся по последнему курсу BNM из кэша. Модуль не импортирует Streamlit.
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from homealize.enrich.currency import to_eur

ALL_PLACES = "Все сёла"


@dataclass(frozen=True)
class Place:
    settlement: str  # ALL_PLACES — все объявления вместе
    region: str | None

    @property
    def label(self) -> str:
        """«Сынжера (Новые Анены)»: одноимённые сёла различаются районом."""
        return f"{self.settlement} ({self.region})" if self.region else self.settlement


@dataclass(frozen=True)
class WeeklyMedian:
    week: date  # понедельник недели по местному времени
    place: Place
    n: int
    median: float  # €/сотку


@dataclass(frozen=True)
class MarketRow:
    place: Place
    n: int
    median: float
    change_percent: float | None  # к медиане compare_weeks недель назад; None — сравнить не с чем


def week_start(moment: datetime, zone: ZoneInfo) -> date:
    """Понедельник недели, в которую попадает момент, по местному времени."""
    day = moment.astimezone(zone).date()
    return day - timedelta(days=day.weekday())


def weekly_medians(
    conn: sqlite3.Connection,
    *,
    zone: ZoneInfo,
    rates: dict[str, float] | None,
    now: datetime,
    weeks: int,
    min_n: int,
) -> list[WeeklyMedian]:
    """Медианы за последние weeks недель, включая текущую; места, где объявлений меньше min_n, пропускаются."""
    listings = conn.execute(
        "SELECT id, settlement, region, area_sot, first_seen_at, last_seen_at FROM listings "
        "WHERE filter_reason IS NULL AND area_sot > 0"
    ).fetchall()
    history: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for entry in conn.execute(
        "SELECT listing_id, price_original, currency, observed_at FROM price_history "
        "WHERE price_original IS NOT NULL ORDER BY observed_at, id"
    ):
        history[entry["listing_id"]].append(entry)
    current = week_start(now, zone)
    result = []
    for offset in range(weeks - 1, -1, -1):
        week = current - timedelta(weeks=offset)
        start, end = _utc_midnight(week, zone), _utc_midnight(week + timedelta(weeks=1), zone)
        values: dict[Place, list[float]] = defaultdict(list)
        for listing in listings:
            if listing["first_seen_at"] >= end or listing["last_seen_at"] < start:
                continue
            price = _last_before(history.get(listing["id"], []), end)
            eur = to_eur(price["price_original"], price["currency"], rates) if price is not None else None
            if eur is None:
                continue
            per_sot = eur / listing["area_sot"]
            values[Place(ALL_PLACES, None)].append(per_sot)
            if listing["settlement"]:
                values[Place(listing["settlement"], listing["region"])].append(per_sot)
        result += [
            WeeklyMedian(week, place, len(items), round(median(items), 2))
            for place, items in sorted(values.items(), key=lambda item: item[0].label.casefold())
            if len(items) >= min_n
        ]
    return result


def market_table(weekly: list[WeeklyMedian], *, current: date, compare_weeks: int) -> list[MarketRow]:
    """Строки текущей недели: «Все сёла» первыми, дальше по числу объявлений."""
    by_key = {(item.week, item.place): item for item in weekly}
    base_week = current - timedelta(weeks=compare_weeks)
    rows = []
    for item in weekly:
        if item.week != current:
            continue
        base = by_key.get((base_week, item.place))
        change = round((item.median / base.median - 1) * 100, 1) if base is not None else None
        rows.append(MarketRow(item.place, item.n, item.median, change))
    return sorted(rows, key=lambda row: (row.place.settlement != ALL_PLACES, -row.n, row.place.label.casefold()))


def _utc_midnight(day: date, zone: ZoneInfo) -> str:
    """Полночь дня по местному времени — в ISO UTC, как времена в базе."""
    return datetime.combine(day, time(), tzinfo=zone).astimezone(UTC).isoformat()


def _last_before(entries: list[sqlite3.Row], end: str) -> sqlite3.Row | None:
    found = None
    for entry in entries:
        if entry["observed_at"] >= end:
            break
        found = entry
    return found
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `uv run pytest tests/dashboard/test_market.py -q`
Expected: PASS.

- [ ] **Step 5: Все тесты**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/dashboard/market.py tests/dashboard/test_market.py
git commit -m "feat: dashboard market medians by week"
```

---

### Task 4: Страница Streamlit

**Files:**
- Create: `src/homealize/dashboard/ui.py`
- Create: `src/homealize/dashboard/app.py`
- Test: `tests/dashboard/test_app.py`

**Interfaces:**
- Consumes:
  - всё из задач 1–3: `listings.*`, `card.load_card`, `card.set_mark`, `card.MarkBusyError`, `card.Card`, `market.*`;
  - `homealize.dashboard.CONFIG_ENV`, `homealize.dashboard.APP_PATH`;
  - `config.load_settings(path)`, `storage.db.connect(path)`, `storage.repo.last_run(conn)`, `storage.rate_cache.latest_rates(conn) -> tuple[str, dict] | None`;
  - `notify.format`: `NO_SETTLEMENT`, `area`, `money`, `decimal`, `price_line`, `route_line`, `local_time`, `osm_url`;
  - `notify.bot.STATES`, `export.excel.AUTHOR_LABELS`, `scoring.components.EXCLUSION_LABELS`, `FLAG_LABELS`, `UTILITY_NAMES`.
- Produces:
  - `homealize.dashboard.ui.main() -> None` — рисует страницу;
  - `homealize.dashboard.ui.plain(text: str) -> str` — экранирует Markdown;
  - `src/homealize/dashboard/app.py` вызывает `main()`;
  - ключи виджетов, на которые опираются тесты:
    - флажки боковой панели `f_favs`, `f_hidden`, `f_suspicious`, `f_over_budget`;
    - кнопки карточки по ссылке `link_fav_<id>`, `link_hide_<id>`, `link_close`.

- [ ] **Step 1: Написать падающие тесты**

`tests/dashboard/test_app.py`:

```python
import sqlite3
from datetime import UTC, datetime

import pytest
from streamlit.testing.v1 import AppTest

from homealize.dashboard import APP_PATH, CONFIG_ENV
from homealize.dashboard.ui import plain
from homealize.runlock import hold_lock
from homealize.storage import marks, repo
from homealize.storage.db import connect, migrate
from homealize.storage.scores import replace_scores
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
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/dashboard/test_app.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.dashboard.ui'`.

- [ ] **Step 3: Реализация страницы**

`src/homealize/dashboard/app.py`:

```python
"""Файл для `streamlit run`: Streamlit исполняет его заново при каждом действии на странице."""

from homealize.dashboard.ui import main

main()
```

`src/homealize/dashboard/ui.py`:

```python
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
    st.line_chart(frame.pivot(index="Неделя", columns="Село", values="€/сот."), height=320)
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `uv run pytest tests/dashboard/test_app.py -q`
Expected: PASS. Предупреждения Streamlit «missing ScriptRunContext» в выводе — норма для `AppTest`.

Если какой-то тест падает из-за поведения `AppTest`, а не из-за логики страницы, исправлять страницу так, чтобы тест проверял то же поведение. Ослаблять проверки теста нельзя. Если иначе не получается — описать это в отчёте.

- [ ] **Step 5: Все тесты**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/dashboard/ui.py src/homealize/dashboard/app.py tests/dashboard/test_app.py
git commit -m "feat: Streamlit dashboard page with top, map, card and market"
```

---

### Task 5: Команда `dashboard`, автозапуск, README, спека §14

**Files:**
- Modify: `src/homealize/cli.py`
- Modify: `scripts/autostart.ps1` (UTF-8 с BOM)
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` (новый §14 в конце)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `homealize.dashboard.APP_PATH`, `CONFIG_ENV` (задача 1);
  - `Settings.dashboard.host`, `port`, `log_path` (задача 1);
  - существующие в `cli.py`: `BUSY = 4`, `main()`, словарь `handlers`.
- Produces:
  - команда `homealize dashboard`;
  - `cli._dashboard(settings, config_path) -> int`;
  - `cli._run_streamlit(argv: list[str]) -> int` — в тестах подменяется;
  - `cli._port_free(host, port) -> bool`;
  - `cli._redirect_missing_output(path: Path) -> None`.

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_cli.py` к импортам добавить `import os` и `import socket`, а также `from homealize.dashboard import APP_PATH, CONFIG_ENV`. В конец файла:

```python
def dashboard_config(tmp_path: Path, **dashboard) -> Path:
    cfg = write_config(tmp_path)
    lines = "".join(f"  {name}: '{value}'\n" for name, value in dashboard.items())
    cfg.write_text(cfg.read_text(encoding="utf-8") + f"dashboard:\n{lines}", encoding="utf-8")
    return cfg


@pytest.fixture
def streamlit_calls(monkeypatch):
    """Настоящий Streamlit не запускается: запоминаем, с какими аргументами его вызвали бы."""
    calls = []
    monkeypatch.setattr(cli, "_run_streamlit", lambda argv: calls.append(list(argv)) or 0)
    monkeypatch.setenv(CONFIG_ENV, "заглушка")  # после теста переменная вернётся к прежнему значению
    return calls


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_dashboard_starts_streamlit_with_the_settings(tmp_path, capsys, streamlit_calls):
    port = free_port()
    cfg = dashboard_config(tmp_path, host="127.0.0.1", port=port)
    assert cli.main(["--config", str(cfg), "dashboard"]) == 0
    (argv,) = streamlit_calls
    assert argv[:3] == ["streamlit", "run", str(APP_PATH)]
    options = dict(zip(argv[3::2], argv[4::2]))
    assert options == {
        "--server.address": "127.0.0.1",
        "--server.port": str(port),
        "--server.headless": "true",
        "--server.fileWatcherType": "none",
        "--server.runOnSave": "false",
        "--browser.gatherUsageStats": "false",
        "--client.toolbarMode": "viewer",
    }
    assert os.environ[CONFIG_ENV] == str(cfg.resolve())
    out = capsys.readouterr().out
    assert f"http://localhost:{port}" in out
    assert "других устройств" not in out


def test_dashboard_on_all_interfaces_mentions_other_devices(tmp_path, capsys, monkeypatch, streamlit_calls):
    monkeypatch.setattr(cli, "_port_free", lambda host, port: True)
    cfg = dashboard_config(tmp_path, host="0.0.0.0", port=free_port())
    assert cli.main(["--config", str(cfg), "dashboard"]) == 0
    assert "других устройств" in capsys.readouterr().out


def test_dashboard_refuses_a_busy_port(tmp_path, capsys, streamlit_calls):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        cfg = dashboard_config(tmp_path, host="127.0.0.1", port=busy.getsockname()[1])
        assert cli.main(["--config", str(cfg), "dashboard"]) == 4
    assert streamlit_calls == []
    assert "уже запущен" in capsys.readouterr().err


def test_dashboard_writes_output_to_a_file_under_pythonw(tmp_path, monkeypatch, streamlit_calls):
    log_path = tmp_path / "logs" / "dashboard.log"
    cfg = dashboard_config(tmp_path, host="127.0.0.1", port=free_port(), log_path=log_path.as_posix())
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert cli.main(["--config", str(cfg), "dashboard"]) == 0
    stream = sys.stdout
    stream.flush()
    assert sys.stderr is stream
    assert "http://localhost" in log_path.read_text(encoding="utf-8")
    stream.close()
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/test_cli.py -q -k dashboard`
Expected: FAIL — `argparse` не знает команду `dashboard`.

- [ ] **Step 3: Команда в `cli.py`**

1. Строку документации модуля дополнить: `… | digest | telegram-test | dashboard.`
2. Импорты: добавить `import os`, `import socket`, а также `from homealize.dashboard import APP_PATH, CONFIG_ENV`.
3. После `commands.add_parser("telegram-test", …)`:

```python
    commands.add_parser("dashboard", help="веб-дашборд: топ, карта, карточки, рынок (работает, пока не остановят)")
```

4. В `handlers`:

```python
        "dashboard": lambda: _dashboard(settings, args.config),
```

5. После `_telegram_client`:

```python
def _dashboard(settings: Settings, config_path: Path) -> int:
    d = settings.dashboard
    _redirect_missing_output(d.log_path)
    if not _port_free(d.host, d.port):
        print(
            f"Порт {d.port} занят: дашборд уже запущен (например, автозапуском) — откройте http://localhost:{d.port}",
            file=sys.stderr,
        )
        return BUSY
    os.environ[CONFIG_ENV] = str(config_path.resolve())
    others = " и с других устройств в этой сети по IP-адресу компьютера" if d.host == "0.0.0.0" else ""
    print(f"Дашборд: http://localhost:{d.port}{others}. Остановить: Ctrl+C.", flush=True)
    return _run_streamlit([
        "streamlit", "run", str(APP_PATH),
        "--server.address", d.host,
        "--server.port", str(d.port),
        "--server.headless", "true",
        "--server.fileWatcherType", "none",
        "--server.runOnSave", "false",
        "--browser.gatherUsageStats", "false",
        "--client.toolbarMode", "viewer",
    ])


def _run_streamlit(argv: list[str]) -> int:
    """Запускает Streamlit в этом процессе; возвращает код выхода, когда сервер остановят."""
    from streamlit.web import cli as streamlit_cli  # тяжёлый импорт — только для команды dashboard

    sys.argv = argv
    try:
        streamlit_cli.main()
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def _port_free(host: str, port: int) -> bool:
    """Свободен ли порт: второй дашборд на том же порту не запустится."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _redirect_missing_output(path: Path) -> None:
    """Под pythonw (автозапуск) sys.stdout и sys.stderr — None: вывод Streamlit и ошибки пишутся в файл."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a", encoding="utf-8", buffering=1)  # открыт до конца процесса
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Автозапуск**

Переписать `scripts/autostart.ps1` целиком. Файл сохраняется в UTF-8 **с BOM**: если инструмент записи не ставит BOM, записать файл командой PowerShell `[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $true))`. Содержимое:

```powershell
<#
.SYNOPSIS
  Автозапуск Homealize при входе в Windows, без окон. Задача «Homealize» запускает `homealize serve`
  (прогоны и Telegram-бот), задача «Homealize Dashboard» — `homealize dashboard` (веб-дашборд).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Install
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove
#>
param(
    [switch]$Install,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Tasks = [ordered]@{
    "Homealize"           = @{ Command = "serve"; Description = "Homealize: прогоны 999.md по расписанию и Telegram-бот" }
    "Homealize Dashboard" = @{ Command = "dashboard"; Description = "Homealize: веб-дашборд (http://localhost:8501)" }
}
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$User = "$env:USERDOMAIN\$env:USERNAME"

if ($Install -eq $Remove) {
    Write-Host "Укажите -Install (включить автозапуск) или -Remove (выключить)."
    exit 2
}

if ($Remove) {
    foreach ($name in $Tasks.Keys) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Задача «$name» остановлена и удалена."
        } else {
            Write-Host "Задачи «$name» нет — удалять нечего."
        }
    }
    exit 0
}

if (-not (Test-Path $Pythonw)) {
    Write-Host "Не найден $Pythonw — выполните в корне проекта: uv sync"
    exit 1
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Host "Нет файла .env с TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID — см. README, раздел «Telegram»."
    exit 1
}

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$options = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
foreach ($name in $Tasks.Keys) {
    $task = $Tasks[$name]
    $action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m homealize.cli $($task.Command)" `
        -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $options `
        -Principal $principal -Description $task.Description -Force | Out-Null
    Start-ScheduledTask -TaskName $name
}
Write-Host "Готово: serve и дашборд запущены и будут стартовать при каждом входе в Windows."
Write-Host "Бот пришлёт «Homealize запущен». Дашборд на этом компьютере: http://localhost:8501"
$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress
foreach ($address in $addresses) {
    Write-Host "С телефона в той же Wi-Fi: http://${address}:8501"
}
Write-Host "Логи: $Root\logs\homealize.log (serve) и $Root\logs\dashboard.log (дашборд)"
```

- [ ] **Step 6: Проверить скрипт, не регистрируя задач**

Run (PowerShell):

```powershell
$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path scripts\autostart.ps1), [ref]$null, [ref]$errors); $errors.Count
(Get-Content scripts\autostart.ps1 -Encoding Byte -TotalCount 3) -join ","
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1; $LASTEXITCODE
```

Expected: `0`; `239,187,191` (BOM); подсказка «Укажите -Install … или -Remove …» и код `2`. Запускать с `-Install` и `-Remove` **нельзя**.

- [ ] **Step 7: README**

Правки в `README.md`:

1. В первом абзаце после «…новые участки, снижения цен и дайджест.» добавить: «Веб-дашборд показывает рейтинг с фильтрами, карту, карточки участков и рынок по сёлам.»
2. Строку `- База: \`data/homealize.db\`. Логи: \`logs/homealize.log\`.` заменить на `- База: \`data/homealize.db\`. Логи: \`logs/homealize.log\`, у дашборда — \`logs/dashboard.log\`.`
3. В разделе «Расписание и автозапуск» заменить абзац «Скрипт регистрирует задачу Планировщика «Homealize» и сразу её запускает, а бот присылает «Homealize запущен». Убрать автозапуск и остановить serve:» на:

   > Скрипт регистрирует две задачи Планировщика — «Homealize» (serve) и «Homealize Dashboard» (дашборд) — и сразу их запускает. Бот присылает «Homealize запущен», а скрипт печатает адреса дашборда для этого компьютера и для телефона. Убрать автозапуск и остановить serve и дашборд:

4. Перед разделом «## Команды» вставить раздел:

````markdown
## Дашборд

`uv run homealize dashboard` запускает веб-дашборд: откройте в браузере http://localhost:8501. Он работает, пока его не остановят (Ctrl+C), а при автозапуске стартует сам.

- **Топ** — рейтинг с фильтрами слева: цена, площадь, минуты до города, обязательные коммуникации, сёла, только избранное. Переключатели добавляют скрытые, подозрительно дешёвые и дороже бюджета. Нажмите на строку — ниже откроется карточка.
- **Карта** — участки под теми же фильтрами. Цвет точки — балл (красный 0, жёлтый 50, зелёный 100), синие точки — въезды в Кишинёв. Нажмите на точку — откроется карточка.
- **Карточка** — фото, из чего сложился балл, метки, история цены, ближайшие магазин, школа, аптека и остановка, ссылки на 999.md и карту. Кнопки «★ В избранное» и «Скрыть» работают так же, как `/fav` и `/hide` в боте. Пока идёт прогон (1–2 минуты), отметку поставить нельзя — повторите чуть позже.
- **Рынок** — медиана €/сотку по сёлам на этой неделе, её изменение за 4 недели и график по неделям.

Данные меняются после каждого прогона — обновите страницу. Карточку можно открыть ссылкой `http://localhost:8501/?ad=<id>`.

### Дашборд с телефона

В `config/settings.yaml` стоит `dashboard.host: "0.0.0.0"`, поэтому дашборд открывается с телефона в той же Wi-Fi по адресу `http://<IP компьютера>:8501`; `autostart.ps1 -Install` печатает этот адрес. Пароля нет: любой в этой сети может открыть дашборд и ставить отметки. Чтобы открывать только с этого компьютера, поставьте `host: "127.0.0.1"` и перезапустите дашборд: `autostart.ps1 -Remove`, затем `autostart.ps1 -Install`.

Чтобы телефон достучался:

1. Домашняя сеть в Windows должна быть **частной**: «Параметры» → «Сеть и Интернет» → Wi-Fi → свойства сети → «Частная сеть». В общественных сетях (кафе, офис) Windows дашборд не откроет — так и задумано.
2. При первом запуске дашборда Windows спросит, разрешить ли Python доступ к сети: отметьте только «Частные сети» и нажмите «Разрешить доступ». Если вы нажали «Отмена», откройте «Брандмауэр Защитника Windows» → «Разрешение взаимодействия с приложением или компонентом…» и включите `pythonw.exe` для частных сетей.
3. Если вопроса не было, а телефон страницу не открывает, выполните в PowerShell от имени администратора:

   ```powershell
   New-NetFirewallRule -DisplayName "Homealize dashboard" -Direction Inbound -Protocol TCP -LocalPort 8501 -Profile Private -RemoteAddress LocalSubnet -Action Allow
   ```
````

5. В таблицу «Команды» после строки `telegram-test` добавить: `| \`dashboard\` | веб-дашборд на http://localhost:8501 (работает, пока не остановят) |`
6. Код выхода `4` заменить на: «`4` — уже идёт другой прогон, serve уже запущен или порт дашборда занят (дашборд уже работает).»

- [ ] **Step 8: Спека §14**

В конец `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` добавить:

```markdown

## 14. Уточнения при планировании этапа 4 (2026-11-02)

Этот раздел уточняет разделы 4, 7.2 и 10; где он расходится с ними, действует он.

- **Запуск.** `homealize dashboard` запускает Streamlit в том же процессе (`streamlit run src/homealize/dashboard/app.py`); путь к настройкам передаётся в переменной окружения `HOMEALIZE_CONFIG`. Адрес и порт задаёт секция `dashboard`: по умолчанию `127.0.0.1:8501`, в `config/settings.yaml` — `0.0.0.0` (выбор пользователя: открывать с телефона в домашней Wi-Fi, без пароля; Windows пропускает соединения только в частных сетях). Если порт занят, команда выходит с кодом 4.
- **Автозапуск.** `scripts/autostart.ps1 -Install` регистрирует вторую задачу «Homealize Dashboard» (`pythonw -m homealize.cli dashboard`), `-Remove` удаляет обе. Под pythonw вывод дашборда пишется в `logs/dashboard.log` (без ротации).
- **Данные.** Страница читает базу заново при каждом действии (SQLite WAL), без кэша; нужна схема не ниже v3.
- **Топ.** В таблице объявления в рейтинге; переключатели добавляют скрытые, подозрительно дешёвые и дороже бюджета. Остальные исключённые (далеко, нет маршрута или координат, дубликат, цена не в евро) видны только в Excel. Фильтры действуют и на карту. У главного объявления группы дубликатов в метках написано «ещё N объявлений этого участка».
- **Карточка** открывается выбором строки таблицы, точки на карте или ссылкой `?ad=<id>`. Фото загружает браузер с сайта 999.md. «Рядом» — ближайший объект каждой категории не дальше `scoring.poi_zero_m`, с названием из OpenStreetMap.
- **Отметки ★ и «скрыть»** пишутся под блокировкой прогона `data/run.lock` без ожидания: если идёт прогон, отметка не ставится и страница просит повторить. «Скрыть» сразу пересчитывает места, как `/hide`.
- **Рынок.**
  - Медиана €/сотку по неделям (понедельник — воскресенье по Кишинёву) за `dashboard.market_weeks` недель.
  - В неделю входят объявления, которые были на сайте хотя бы раз за неделю, с последней ценой до её конца. Учитываются все, прошедшие фильтры типа и площади, включая дороже бюджета. Цены не в евро — по последнему курсу BNM.
  - Село показывается при `n ≥ dashboard.market_min_n` (3), строка «Все сёла» — по всем объявлениям. В таблице — изменение медианы за `dashboard.market_compare_weeks` (4) недели.
- **Стек.** `streamlit>=1.66`, `pydeck>=0.9` (подложка CARTO без ключа), `pandas>=2.2`.
- **Коды выхода:** `4` также означает, что порт дашборда занят.
```

- [ ] **Step 9: Все тесты**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/homealize/cli.py tests/test_cli.py scripts/autostart.ps1 README.md docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md
git commit -m "feat: homealize dashboard command, autostart task, docs"
```

---

## Проверка после всех задач (контролёр)

Делает контролёр, не исполнители. Настоящую базу и автозапуск это не трогает.

1. `uv run pytest -q` — все тесты зелёные.
2. Скопировать `data/homealize.db` во временную папку (scratchpad) и написать там `settings.yaml` с `storage.db_path` и `storage.run_lock_path` на копию и `dashboard: {host: "127.0.0.1", port: 8599, log_path: …/dashboard.log}`.
3. Запустить в фоне `uv run homealize --config <этот settings.yaml> dashboard` и открыть `http://localhost:8599` во встроенном браузере. Проверить:
   - вкладки и таблица (около 523 строк);
   - фильтры, в том числе «дороже бюджета»;
   - карта с цветными точками и подложкой;
   - карточка по выбору строки и точки и по `?ad=<id>`;
   - ★ и «Скрыть» на копии базы;
   - вкладка «Рынок»;
   - вид на ширине телефона (`resize_window` mobile).
4. Остановить фоновый процесс. Порт 8501 и задачи Планировщика не трогать: дашборд в автозапуск пользователь включит сам командой `scripts\autostart.ps1 -Install`.
