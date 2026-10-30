# Этап 2 «Оценка и Excel» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** после обхода 999.md каждый активный участок получает балл 0–100 по «весам» спеки (цена относительно рынка, дорога до Кишинёва, инфраструктура рядом, коммуникации, рейтинг села), а рейтинг выгружается в `exports/latest.xlsx`.

**Architecture:**
- Внешние данные готовятся отдельно и кэшируются в SQLite:
  - объекты инфраструктуры из OpenStreetMap (Overpass), раз в 30 дней;
  - маршруты OSRM до 14 точек въезда в город, один раз на точку;
  - курс Нацбанка, раз в день.
- Чистый движок оценки (`scoring/engine.py`) по объявлениям и этим данным считает медианы, компоненты, исключения и места. Результат он записывает снимком в таблицу `scores`.
- `export/excel.py` строит из `scores` книгу Excel.
- `evaluate.py` связывает всё это, CLI вызывает его после обхода, а также командами `rescore`, `export`, `refresh-poi`.

**Tech Stack:** Python 3.12, uv, httpx, pydantic v2, PyYAML, sqlite3, openpyxl (новая зависимость), pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` (этап 2 из §11; модель оценки §6; Excel §7.3; данные §5). Уточнения этого плана записываются в спеку новым §12 (задача 10).

**Предыдущий этап:** этап 1 в `main` (128 офлайн-тестов, 1 live). Отложенные замечания: `docs/superpowers/notes/2026-10-29-stage1-followups.md`.

## Решения, принятые при подготовке плана

Проверено на реальных данных 2026-10-30. Эти решения обязательны для исполнителей.

1. **Статус объявления не трогаем.** `listings.status` и `filter_reason` по-прежнему отражают только фильтры этапа 1 и жизненный цикл. Исключения этапа 2 (дальше 30 км, дороже бюджета, подозрительно дёшево, дубликат, нет координат/маршрута, цену нельзя перевести в евро) пишутся в `scores.excluded_reason` и пересчитываются каждый раз. Так статус не «скачет» между прогонами.
2. **Id 999.md.**
   - В `listings` добавляются `region_id` и `settlement_id`. На сайте есть одноимённые сёла в разных районах (Ниморены, Гратиешты, Бошкана, Оницканы, Максимовка, Рошканы).
   - Медианы и справочник сёл работают по `settlement_id`.
   - `filters.allowed_regions` остаётся списком названий: так удобнее править настройки.
3. **Формат цен проверен.** У всех 1779 объявлений `mode = PM_FIXED` и `measurement = unit`, цен «за сотку» нет, отдельная обработка не нужна. Валюты: 1773 EUR, 5 MDL, 1 USD. MDL и USD переводятся в евро по курсу BNM при оценке (`scores.price_eur`), колонка `listings.price_eur` не меняется.
4. **Точки въезда:** 14 мест, где магистрали пересекают границу города Кишинёв (OSM relation 1748490, admin_level 8). Вычислены по данным OSM и лежат в настройках. Участки в черте города (`settlement_id` 13859, «Кишинёв») получают 0 км и 0 мин без запроса к OSRM.
5. **Справочник сёл:**
   - Хранится в `config/settlements.yaml` (не в `data/`, где лежат файлы прогонов); ключ — `settlement_id`; 18 сёл из `code_artifact.md`.
   - Читается при каждой оценке, таблицы в базе для него нет.
   - Сёла вне справочника получают 50 баллов и метку «не изучено».
6. **Таблицы.**
   - Одна таблица-снимок `scores` вместо `enrichment` + `scores` из спеки. Плюс три кэша: `poi`, `route_cache`, `fx_rates`.
   - Дубликаты не хранятся в `listings`, они записываются в `scores.duplicate_group` / `is_group_primary`.
7. **Координаты без точки на карте:**
   - Используется медиана координат объявлений того же села, с меткой «координаты приблизительные».
   - Если в селе нет ни одного объявления с координатами, участок исключается («нет координат»). На 2026-10-30 так только 3 объявления.
8. **Скрытые объявления** (`user_marks`) переносятся на этап 3 вместе с ботом: на этапе 2 их нечем отмечать.
9. **Коды выхода CLI:**
   - `0` — всё успешно;
   - `1` — обход прерван;
   - `2` — настройки не прочитаны;
   - `3` — обход прошёл, но оценка или Excel не удались.
10. **Excel:** дневная копия называется по дате UTC.

## Global Constraints

- Python `>=3.12`, запуск только через `uv`. Новая зависимость одна — `openpyxl`.
- Запросы к 999.md не меняются: по одному, с паузой 2–3 с. `feature(id: 16)` и `owner` не запрашиваются.
- **Внешние сервисы:**
  - OSRM — не больше 100 координат в одном табличном запросе (`batch_size` + число точек въезда ≤ 100), пауза между запросами ≥ 1 с, результаты кэшируются навсегда по округлённым координатам (3 знака, ~100 м).
  - Overpass — один запрос раз в 30 дней.
  - BNM — один запрос в день.
  - Заголовок `User-Agent: homealize/0.1 (personal land monitor)`.
- Ошибка внешнего сервиса никогда не прерывает оценку: используются кэшированные данные, а в результат добавляется русское «предупреждение».
- **Приватность:** из OSM сохраняются только категория, название, бренд и координаты. Телефоны и сайты организаций не сохраняются.
- **Модель оценки (спека §6), все числа в `config/settings.yaml`:**
  - веса: цена 0.30, дорога 0.20, инфраструктура 0.20, коммуникации 0.20, село 0.10;
  - бюджет 50 000 €, предел 30 км по дорогам до въезда;
  - цена: на 40% дешевле медианы — 100, на уровне медианы — 50, на 40% дороже — 0; «подозрительно дёшево» — дешевле 25% медианы;
  - медиана: по селу при n ≥ 5, иначе по участкам ±3 км при n ≥ 8, иначе по всему региону; в выборку входят также объявления, снятые за последние 90 дней;
  - дорога: ≤ 5 мин — 100, ≥ 30 мин — 0;
  - инфраструктура: ≤ 500 м — полный балл категории, ≥ 1500 м — 0; веса категорий: супермаркет 0.30, школа 0.20, детсад 0.15, аптека 0.15, остановка 0.10, медпункт 0.10;
  - коммуникации: свет 35, газ 30, вода 25, канализация 10;
  - дубликаты: ≤ 50 м, та же площадь, цена ±10%.
- Тексты для пользователя (пояснения, метки, CLI, предупреждения) — на русском.
- Время в базе — ISO 8601 с часовым поясом. Оценка получает `now` снаружи.
- Тесты не ходят в сеть, кроме `pytest -m live`.

## Структура файлов

```
config/settings.yaml                     + секции routing, poi, currency, scoring, export
config/settlements.yaml                  НОВЫЙ: справочник 18 сёл (id 999.md → потенциал, балл)
src/homealize/config.py                  + RoutingSettings, EntryPoint, PoiSettings, CurrencySettings,
                                           ScoringSettings, ScoringWeights, ExportSettings
src/homealize/settlements.py             НОВЫЙ: SettlementRef, load_settlements()
src/homealize/models.py                  + Listing.region_id/settlement_id, Poi, Route
src/homealize/sources/nine99/parse.py    + id региона и села
src/homealize/storage/repo.py            + region_id/settlement_id в _content
src/homealize/storage/migrations/002_enrichment.sql   НОВЫЙ
src/homealize/storage/poi_cache.py       НОВЫЙ: POI в базе
src/homealize/storage/route_cache.py     НОВЫЙ: кэш маршрутов
src/homealize/storage/rate_cache.py      НОВЫЙ: курсы BNM
src/homealize/storage/scores.py          НОВЫЙ: запись и чтение снимка оценки
src/homealize/geo.py                     НОВЫЙ: haversine_m()
src/homealize/enrich/__init__.py         НОВЫЙ
src/homealize/enrich/poi.py              НОВЫЙ: запрос Overpass, классификация, PoiIndex, ensure_pois()
src/homealize/enrich/routing.py          НОВЫЙ: OsrmRouter, ensure_routes(), round_key()
src/homealize/enrich/currency.py         НОВЫЙ: курс BNM, ensure_rates(), to_eur()
src/homealize/scoring/__init__.py        НОВЫЙ
src/homealize/scoring/market.py          НОВЫЙ: медианы по уровням, «подозрительно дёшево», дубликаты
src/homealize/scoring/components.py      НОВЫЙ: компоненты балла, пояснения, метки
src/homealize/scoring/engine.py          НОВЫЙ: rescore(), route_points()
src/homealize/export/__init__.py         НОВЫЙ
src/homealize/export/excel.py            НОВЫЙ: export_workbook()
src/homealize/evaluate.py                НОВЫЙ: evaluate() — внешние данные + rescore
src/homealize/cli.py                     + оценка после run; команды rescore, export, refresh-poi
tests/test_settlements.py, tests/test_geo.py, tests/test_evaluate.py              НОВЫЕ
tests/enrich/{__init__,test_poi,test_routing,test_currency}.py                    НОВЫЕ
tests/scoring/{__init__,test_market,test_components,test_engine}.py               НОВЫЕ
tests/export/{__init__,test_excel}.py                                              НОВЫЕ
tests/live/test_live_services.py                                                   НОВЫЙ
README.md, спека §12                                                               ОБНОВЛЕНИЕ
```

---

### Task 1: Настройки этапа 2 и справочник сёл

**Files:**
- Modify: `src/homealize/config.py` (заменить целиком), `config/settings.yaml` (заменить целиком)
- Create: `config/settlements.yaml`, `src/homealize/settlements.py`
- Test: `tests/test_config.py` (добавить тесты), `tests/test_settlements.py`

**Interfaces:**
- Consumes: существующий `homealize.config` этапа 1.
- Produces:
  - Константы в `homealize.config`: `POI_CATEGORIES = ("supermarket", "school", "kindergarten", "pharmacy", "bus_stop", "clinic")`, `UTILITIES = ("electricity", "gas", "water", "sewerage")`, `CHISINAU_SETTLEMENT_ID = 13859`, `OSRM_MAX_COORDINATES = 100`.
  - Модели:
    - `EntryPoint(name, lat, lon)`;
    - `RoutingSettings(osrm_url, batch_size, pause_seconds, timeout_seconds, max_route_km, city_settlement_ids, entry_points)`;
    - `PoiSettings(overpass_url, center_lat, center_lon, radius_km, refresh_days, timeout_seconds, chains)`;
    - `CurrencySettings(bnm_url, timeout_seconds)`;
    - `ScoringWeights(price, route, infrastructure, utilities, settlement)`;
    - `ScoringSettings`, поля: `weights, settlements_path, max_price_eur, price_full_discount, suspicious_ratio, median_min_settlement, median_min_route_band, median_route_band_km, median_removed_days, route_best_min, route_worst_min, poi_full_m, poi_zero_m, poi_weights, utility_weights, unknown_settlement_score, duplicate_distance_m, duplicate_price_tolerance`;
    - `ExportSettings(dir, keep_days)`.
  - `Settings` получает поля `routing`, `poi`, `currency`, `scoring`, `export`.
  - `homealize.settlements.SettlementRef(id: int, name: str, potential: str, score: float)` (frozen), `load_settlements(path: Path) -> dict[int, SettlementRef]`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_config.py` (импорт расширить до `from homealize.config import POI_CATEGORIES, UTILITIES, Settings, load_settings`):

```python
def test_stage2_defaults_match_spec():
    s = Settings()
    w = s.scoring.weights
    assert (w.price, w.route, w.infrastructure, w.utilities, w.settlement) == (0.30, 0.20, 0.20, 0.20, 0.10)
    assert s.scoring.max_price_eur == 50000
    assert s.routing.max_route_km == 30
    assert len(s.routing.entry_points) == 14
    assert s.routing.city_settlement_ids == [13859]
    assert set(s.scoring.poi_weights) == set(POI_CATEGORIES)
    assert set(s.scoring.utility_weights) == set(UTILITIES)
    assert s.export.dir == Path("exports")


def test_project_settings_file_matches_defaults():
    assert load_settings(PROJECT_ROOT / "config" / "settings.yaml") == Settings()


def test_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Settings.model_validate({"scoring": {"weights": {"price": 0.5}}})


def test_osrm_table_size_is_limited():
    with pytest.raises(ValidationError):
        Settings.model_validate({"routing": {"batch_size": 90}})


def test_chain_names_must_be_plain():
    with pytest.raises(ValidationError):
        Settings.model_validate({"poi": {"chains": ["Nr. 1"]}})


def test_unknown_poi_category_is_rejected():
    with pytest.raises(ValidationError):
        Settings.model_validate({"scoring": {"poi_weights": {"bank": 1.0}}})
```

`tests/test_settlements.py`:

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from homealize.settlements import load_settlements

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_reference_loads():
    refs = load_settlements(PROJECT_ROOT / "config" / "settlements.yaml")
    assert len(refs) == 18
    assert (refs[13932].name, refs[13932].score) == ("Яловены", 100)
    assert refs[13917].score == 40
    assert refs[14398].potential == "Высокий"


def test_duplicate_id_is_rejected(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        "- {id: 1, name: A, potential: X, score: 50}\n- {id: 1, name: B, potential: Y, score: 60}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="дважды"):
        load_settlements(path)


def test_score_out_of_range_is_rejected(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("- {id: 1, name: A, potential: X, score: 150}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settlements(path)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_config.py tests/test_settlements.py -v`
Expected: FAIL — `ImportError: cannot import name 'POI_CATEGORIES'` и `ModuleNotFoundError: No module named 'homealize.settlements'`.

- [ ] **Step 3: Заменить `src/homealize/config.py` целиком**

```python
"""Настройки Homealize: читаются из config/settings.yaml и проверяются pydantic."""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_REGIONS = [
    "Кишинёв мун.",
    "Яловены",
    "Страшены",
    "Новые Анены",
    "Криуляны",
    "Хынчешты",
    "Оргеев",
]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
POI_CATEGORIES = ("supermarket", "school", "kindergarten", "pharmacy", "bus_stop", "clinic")
UTILITIES = ("electricity", "gas", "water", "sewerage")
CHISINAU_SETTLEMENT_ID = 13859  # «Кишинёв» на 999.md: участки в черте города
OSRM_MAX_COORDINATES = 100  # лимит табличного запроса публичного OSRM
# Где магистрали пересекают границу города Кишинёв (OSM relation 1748490, admin_level 8), 2026-10-30.
DEFAULT_ENTRY_POINTS = [
    {"name": "R1 Calea Trușenilor", "lat": 47.05563, "lon": 28.76417},
    {"name": "R3 Șoseaua Hâncești", "lat": 46.97861, "lon": 28.77084},
    {"name": "M3 Drumul Băcioiului", "lat": 46.95466, "lon": 28.86559},
    {"name": "R6 Calea Orheiului", "lat": 47.06586, "lon": 28.85704},
    {"name": "M1 окружная (север)", "lat": 47.0747, "lon": 28.81362},
    {"name": "M1 окружная (запад)", "lat": 47.04221, "lon": 28.7564},
    {"name": "R2 Șoseaua Muncești", "lat": 46.92038, "lon": 28.97142},
    {"name": "ул. Тудор Владимиреску", "lat": 47.02461, "lon": 28.78344},
    {"name": "ул. Буковина", "lat": 47.06816, "lon": 28.88644},
    {"name": "R2 Băcioii Noi", "lat": 46.95257, "lon": 28.92021},
    {"name": "бул. Дачия (юг)", "lat": 46.9441, "lon": 28.92314},
    {"name": "бул. Дачия (север)", "lat": 46.95883, "lon": 28.89937},
    {"name": "L450 ул. Дойна", "lat": 47.07476, "lon": 28.83011},
    {"name": "L455 Chișinău – Vadul lui Vodă", "lat": 47.04865, "lon": 28.92514},
]
DEFAULT_CHAINS = ["Linella", "Kaufland", "Fidesco", "Local", "Green Hills", "Velmart", "Nr 1"]
DEFAULT_POI_WEIGHTS = {
    "supermarket": 0.30,
    "school": 0.20,
    "kindergarten": 0.15,
    "pharmacy": 0.15,
    "bus_stop": 0.10,
    "clinic": 0.10,
}
DEFAULT_UTILITY_WEIGHTS = {"electricity": 35.0, "gas": 30.0, "water": 25.0, "sewerage": 10.0}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSettings(_Section):
    base_url: str = "https://999.md"
    page_size: int = Field(78, ge=1, le=100)
    delay_seconds: tuple[float, float] = (2.0, 3.0)
    timeout_seconds: float = Field(30.0, gt=0)
    max_retries: int = Field(3, ge=0)
    backoff_seconds: list[float] = Field(default_factory=lambda: [5.0, 15.0, 45.0])
    user_agent: str = DEFAULT_USER_AGENT

    @model_validator(mode="after")
    def _check_timing(self) -> "SourceSettings":
        low, high = self.delay_seconds
        if low < 0 or high < low:
            raise ValueError("delay_seconds: нужно 0 <= минимум <= максимум")
        if len(self.backoff_seconds) < self.max_retries:
            raise ValueError("backoff_seconds: нужна пауза для каждой попытки из max_retries")
        return self


class FilterSettings(_Section):
    land_type: str = "Участок под строительство"
    area_sot_min: float = 5
    area_sot_max: float = 10
    allowed_regions: list[str] = Field(default_factory=lambda: list(DEFAULT_REGIONS))

    @model_validator(mode="after")
    def _check_area(self) -> "FilterSettings":
        if self.area_sot_min > self.area_sot_max:
            raise ValueError("area_sot_min не может быть больше area_sot_max")
        return self


class StorageSettings(_Section):
    db_path: Path = Path("data/homealize.db")


class LifecycleSettings(_Section):
    removal_missed_scans: int = Field(2, ge=1)


class LoggingSettings(_Section):
    path: Path = Path("logs/homealize.log")
    max_bytes: int = Field(10 * 1024 * 1024, gt=0)
    backup_count: int = Field(5, ge=0)


class EntryPoint(_Section):
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RoutingSettings(_Section):
    osrm_url: str = "https://router.project-osrm.org"
    batch_size: int = Field(80, ge=1)
    pause_seconds: float = Field(1.0, ge=0)
    timeout_seconds: float = Field(60.0, gt=0)
    max_route_km: float = Field(30.0, gt=0)
    city_settlement_ids: list[int] = Field(default_factory=lambda: [CHISINAU_SETTLEMENT_ID])
    entry_points: list[EntryPoint] = Field(
        default_factory=lambda: [EntryPoint(**point) for point in DEFAULT_ENTRY_POINTS]
    )

    @model_validator(mode="after")
    def _check_table_size(self) -> "RoutingSettings":
        if not self.entry_points:
            raise ValueError("entry_points: нужна хотя бы одна точка въезда")
        if self.batch_size + len(self.entry_points) > OSRM_MAX_COORDINATES:
            raise ValueError(f"batch_size + число точек въезда не может превышать {OSRM_MAX_COORDINATES}")
        return self


class PoiSettings(_Section):
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    center_lat: float = 47.0245
    center_lon: float = 28.8327
    radius_km: float = Field(35.0, gt=0)
    refresh_days: int = Field(30, ge=1)
    timeout_seconds: float = Field(240.0, gt=0)
    chains: list[str] = Field(default_factory=lambda: list(DEFAULT_CHAINS))

    @field_validator("chains")
    @classmethod
    def _plain_names(cls, chains: list[str]) -> list[str]:
        for name in chains:
            if not re.fullmatch(r"[\w ]+", name):
                raise ValueError(f"chains: «{name}» — допускаются только буквы, цифры и пробелы")
        return chains


class CurrencySettings(_Section):
    bnm_url: str = "https://www.bnm.md/en/official_exchange_rates"
    timeout_seconds: float = Field(30.0, gt=0)


class ScoringWeights(_Section):
    price: float = Field(0.30, ge=0)
    route: float = Field(0.20, ge=0)
    infrastructure: float = Field(0.20, ge=0)
    utilities: float = Field(0.20, ge=0)
    settlement: float = Field(0.10, ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "ScoringWeights":
        total = self.price + self.route + self.infrastructure + self.utilities + self.settlement
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights: сумма весов должна быть 1, сейчас {total:g}")
        return self


class ScoringSettings(_Section):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    settlements_path: Path = Path("config/settlements.yaml")
    max_price_eur: float = Field(50000.0, gt=0)
    price_full_discount: float = Field(0.40, gt=0)
    suspicious_ratio: float = Field(0.25, gt=0, lt=1)
    median_min_settlement: int = Field(5, ge=1)
    median_min_route_band: int = Field(8, ge=1)
    median_route_band_km: float = Field(3.0, gt=0)
    median_removed_days: int = Field(90, ge=0)
    route_best_min: float = Field(5.0, ge=0)
    route_worst_min: float = Field(30.0, gt=0)
    poi_full_m: float = Field(500.0, ge=0)
    poi_zero_m: float = Field(1500.0, gt=0)
    poi_weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_POI_WEIGHTS))
    utility_weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_UTILITY_WEIGHTS))
    unknown_settlement_score: float = Field(50.0, ge=0, le=100)
    duplicate_distance_m: float = Field(50.0, ge=0)
    duplicate_price_tolerance: float = Field(0.10, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "ScoringSettings":
        if self.route_best_min >= self.route_worst_min:
            raise ValueError("route_best_min должен быть меньше route_worst_min")
        if self.poi_full_m >= self.poi_zero_m:
            raise ValueError("poi_full_m должен быть меньше poi_zero_m")
        unknown = set(self.poi_weights) - set(POI_CATEGORIES)
        if unknown:
            raise ValueError(f"poi_weights: неизвестные категории {sorted(unknown)}")
        unknown = set(self.utility_weights) - set(UTILITIES)
        if unknown:
            raise ValueError(f"utility_weights: неизвестные коммуникации {sorted(unknown)}")
        return self


class ExportSettings(_Section):
    dir: Path = Path("exports")
    keep_days: int = Field(30, ge=1)


class Settings(_Section):
    source: SourceSettings = Field(default_factory=SourceSettings)
    filters: FilterSettings = Field(default_factory=FilterSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    lifecycle: LifecycleSettings = Field(default_factory=LifecycleSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    routing: RoutingSettings = Field(default_factory=RoutingSettings)
    poi: PoiSettings = Field(default_factory=PoiSettings)
    currency: CurrencySettings = Field(default_factory=CurrencySettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)


def load_settings(path: Path) -> Settings:
    """Читает YAML-файл настроек; отсутствующие ключи берутся по умолчанию."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)
```

- [ ] **Step 4: Создать `src/homealize/settlements.py`**

```python
"""Справочник сёл из code_artifact.md: потенциал населённого пункта для оценки (спека §5, §6.5)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SettlementRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int  # номер населённого пункта на 999.md (feature 8)
    name: str
    potential: str  # формулировка из документа
    score: float = Field(ge=0, le=100)


def load_settlements(path: Path) -> dict[int, SettlementRef]:
    """Читает справочник; id сёл должны быть уникальны."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    by_id: dict[int, SettlementRef] = {}
    for item in data:
        ref = SettlementRef.model_validate(item)
        if ref.id in by_id:
            raise ValueError(f"settlements: id {ref.id} встречается дважды")
        by_id[ref.id] = ref
    return by_id
```

- [ ] **Step 5: Создать `config/settlements.yaml`**

```yaml
# Справочник сёл из code_artifact.md (спека §5, §6.5).
# id — номер населённого пункта на 999.md (поле «Населённый пункт»), снято 2026-10-30.
# potential — формулировка из документа; score — балл компонента «рейтинг села».
- {id: 13932, name: "Яловены", potential: "Очень высокий", score: 100}
- {id: 14398, name: "Сынжера", potential: "Высокий", score: 85}
- {id: 12926, name: "Ставчены", potential: "Высокий (но дорогой)", score: 85}
- {id: 13982, name: "Магдачешты", potential: "Высокий", score: 85}
- {id: 14030, name: "Порумбень", potential: "Высокий", score: 85}
- {id: 14415, name: "Страшены", potential: "Высокий в бюджетном сегменте", score: 85}
- {id: 14401, name: "Бачой", potential: "Средне-высокий", score: 70}
- {id: 12919, name: "Гидигич", potential: "Средне-высокий", score: 70}
- {id: 12934, name: "Ватра", potential: "Средне-высокий", score: 70}
- {id: 12985, name: "Пересечино", potential: "Выше среднего (для бюджетного сегмента)", score: 70}
- {id: 13921, name: "Костешть", potential: "Средний", score: 55}
- {id: 13501, name: "Резены", potential: "Средний", score: 55}
- {id: 12952, name: "Трушены", potential: "Средний", score: 55}
- {id: 14031, name: "Криково", potential: "Стабильный", score: 55}
- {id: 14062, name: "Вадул-луй-Водэ", potential: "Специфический", score: 45}
- {id: 13942, name: "Кодру", potential: "Умеренный (перегретый рынок)", score: 40}
- {id: 13917, name: "Дурлешты", potential: "Умеренный (из-за перенаселённости)", score: 40}
- {id: 13933, name: "Думбрава", potential: "Умеренный (из-за перенаселённости)", score: 40}
```

- [ ] **Step 6: Заменить `config/settings.yaml` целиком**

Значения обязаны совпадать со значениями по умолчанию в `config.py`: это проверяет `test_project_settings_file_matches_defaults`.

```yaml
# Настройки Homealize. После правки просто перезапустите команду.

source:
  base_url: "https://999.md"
  page_size: 78                  # столько объявлений сайт отдаёт на страницу
  delay_seconds: [2.0, 3.0]      # пауза между запросами, случайная в этом диапазоне
  timeout_seconds: 30
  max_retries: 3
  backoff_seconds: [5, 15, 45]   # паузы перед повторами при 429/5xx/таймауте
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

filters:
  land_type: "Участок под строительство"
  area_sot_min: 5
  area_sot_max: 10
  allowed_regions:               # поле «Регион» объявления на 999.md
    - "Кишинёв мун."
    - "Яловены"
    - "Страшены"
    - "Новые Анены"
    - "Криуляны"
    - "Хынчешты"
    - "Оргеев"

storage:
  db_path: "data/homealize.db"

lifecycle:
  removal_missed_scans: 2        # снимать объявление после стольких полных проходов без него

logging:
  path: "logs/homealize.log"
  max_bytes: 10485760
  backup_count: 5

routing:
  osrm_url: "https://router.project-osrm.org"   # публичный сервер; на VPS (этап 5) — свой контейнер
  max_route_km: 30                              # дальше от въезда в город — участок не попадает в рейтинг
  city_settlement_ids: [13859]                  # «Кишинёв»: участки в черте города получают 0 км
  entry_points:                                 # где магистрали пересекают границу города (OSM, 2026-10-30)
    - {name: "R1 Calea Trușenilor", lat: 47.05563, lon: 28.76417}
    - {name: "R3 Șoseaua Hâncești", lat: 46.97861, lon: 28.77084}
    - {name: "M3 Drumul Băcioiului", lat: 46.95466, lon: 28.86559}
    - {name: "R6 Calea Orheiului", lat: 47.06586, lon: 28.85704}
    - {name: "M1 окружная (север)", lat: 47.0747, lon: 28.81362}
    - {name: "M1 окружная (запад)", lat: 47.04221, lon: 28.7564}
    - {name: "R2 Șoseaua Muncești", lat: 46.92038, lon: 28.97142}
    - {name: "ул. Тудор Владимиреску", lat: 47.02461, lon: 28.78344}
    - {name: "ул. Буковина", lat: 47.06816, lon: 28.88644}
    - {name: "R2 Băcioii Noi", lat: 46.95257, lon: 28.92021}
    - {name: "бул. Дачия (юг)", lat: 46.9441, lon: 28.92314}
    - {name: "бул. Дачия (север)", lat: 46.95883, lon: 28.89937}
    - {name: "L450 ул. Дойна", lat: 47.07476, lon: 28.83011}
    - {name: "L455 Chișinău – Vadul lui Vodă", lat: 47.04865, lon: 28.92514}

poi:
  radius_km: 35                  # инфраструктура скачивается в этом радиусе от центра Кишинёва
  refresh_days: 30
  chains: ["Linella", "Kaufland", "Fidesco", "Local", "Green Hills", "Velmart", "Nr 1"]

scoring:
  weights: {price: 0.30, route: 0.20, infrastructure: 0.20, utilities: 0.20, settlement: 0.10}
  settlements_path: "config/settlements.yaml"
  max_price_eur: 50000           # бюджет: дороже — не в рейтинге, но учитывается в медианах
  suspicious_ratio: 0.25         # дешевле этой доли медианы — «подозрительно дёшево»
  route_best_min: 5              # до въезда за столько минут или быстрее — 100 баллов
  route_worst_min: 30            # столько минут или дольше — 0 баллов
  poi_weights: {supermarket: 0.30, school: 0.20, kindergarten: 0.15, pharmacy: 0.15, bus_stop: 0.10, clinic: 0.10}
  utility_weights: {electricity: 35, gas: 30, water: 25, sewerage: 10}

export:
  dir: "exports"
  keep_days: 30                  # сколько дней хранить дневные копии Excel
```

- [ ] **Step 7: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_config.py tests/test_settlements.py -v`
Expected: все проходят (7 старых + 6 новых в test_config, 3 в test_settlements).

Run: `uv run pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 8: Commit**

```bash
git add src/homealize/config.py src/homealize/settlements.py config/settings.yaml config/settlements.yaml tests/test_config.py tests/test_settlements.py
git commit -m "feat: stage 2 settings and settlement reference"
```

---

### Task 2: Id района и села с 999.md и миграция 002

**Files:**
- Modify: `src/homealize/models.py` (поля `Listing`), `src/homealize/sources/nine99/parse.py`, `src/homealize/storage/repo.py` (`_content`)
- Create: `src/homealize/storage/migrations/002_enrichment.sql`
- Test: `tests/nine99/test_parse.py`, `tests/storage/test_db.py`, `tests/storage/test_repo.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Listing`, `parse.to_listing`, `repo._content`, `db.migrate` (этап 1).
- Produces:
  - `Listing.region_id: int | None`, `Listing.settlement_id: int | None`.
  - Колонки `listings.region_id`, `listings.settlement_id`.
  - Таблицы:
    - `poi(osm_key, category, name, brand, lat, lon, fetched_at)`;
    - `route_cache(lat_round, lon_round, route_km, route_min, entry, computed_at)`;
    - `fx_rates(rate_date, currency, mdl_per_unit)`;
    - `scores(...)` — колонки в SQL ниже.
  - Версия схемы 2.

- [ ] **Step 1: Написать падающие тесты**

В `tests/nine99/test_parse.py` добавить:

```python
def _listings_by_id(name):
    return {listing.id: listing for listing in parse_search_response(load_fixture(name)).listings}


def test_option_ids_are_kept():
    ads = _listings_by_id("search_page_1.json")
    assert (ads["100000001"].region_id, ads["100000001"].settlement_id) == (12900, 14010)
    assert (ads["100000002"].region_id, ads["100000002"].settlement_id) == (12920, 13700)


def test_non_numeric_option_id_gives_none():
    payload = load_fixture("search_page_1.json")
    payload["data"]["searchAds"]["ads"][0]["city"]["value"]["value"] = "x"
    ad = parse_search_response(payload).listings[0]
    assert ad.settlement_id is None
    assert ad.settlement == "Гратиешты"
```

В `tests/storage/test_db.py` заменить тесты `test_migrate_creates_schema_v1` и `test_migrate_twice_is_noop` на эти и добавить тест обновления:

```python
def test_migrate_creates_latest_schema():
    conn = connect(":memory:")
    assert migrate(conn) == 2
    assert {"listings", "price_history", "runs", "poi", "route_cache", "fx_rates", "scores"} <= tables(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(listings)")}
    assert {"region_id", "settlement_id"} <= columns


def test_migrate_twice_is_noop():
    conn = connect(":memory:")
    migrate(conn)
    assert migrate(conn) == 2


def test_upgrade_from_v1_keeps_listings(tmp_path):
    from importlib import resources

    conn = connect(tmp_path / "old.db")
    try:
        v1 = resources.files("homealize.storage.migrations").joinpath("001_initial.sql").read_text(encoding="utf-8")
        conn.executescript(f"BEGIN;\n{v1}\nPRAGMA user_version = 1;\nCOMMIT;")
        conn.execute(
            "INSERT INTO listings (id, source, url, title, author_type, first_seen_at, last_seen_at, status) "
            "VALUES ('a', '999', 'u', 't', 'private', 'now', 'now', 'active')"
        )
        conn.commit()
        assert migrate(conn) == 2
        row = conn.execute("SELECT id, region_id, settlement_id FROM listings").fetchone()
        assert (row["id"], row["region_id"], row["settlement_id"]) == ("a", None, None)
    finally:
        conn.close()
```

В `tests/storage/test_repo.py` добавить:

```python
def test_region_and_settlement_ids_are_saved(db):
    save(db, make_listing(region_id=12900, settlement_id=14010))
    row = repo.get_listing(db, "100000001")
    assert (row["region_id"], row["settlement_id"]) == (12900, 14010)
```

В `tests/test_cli.py` в `test_init_db_creates_database` заменить `"схема v1"` на `"схема v2"`.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/nine99/test_parse.py tests/storage tests/test_cli.py -v`
Expected: FAIL — нет атрибута `region_id`, `migrate` возвращает 1, `схема v1`.

- [ ] **Step 3: Реализовать**

В `src/homealize/models.py` в `Listing` сразу после строки `settlement: str | None = None` добавить:

```python
    region_id: int | None = None  # номер опции «Регион» на сайте
    settlement_id: int | None = None  # номер опции «Населённый пункт» на сайте
```

В `src/homealize/sources/nine99/parse.py`:
- в `to_listing` сразу после строки `settlement=_option(raw.city),` добавить:

```python
        region_id=_option_id(raw.region),
        settlement_id=_option_id(raw.city),
```

- после функции `_option` добавить:

```python
def _option_id(feature: Feature[OptionValue] | None) -> int | None:
    option = _value(feature)
    value = option.value if option else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None
```

В `src/homealize/storage/repo.py` в `_content` сразу после строки `"settlement": listing.settlement,` добавить:

```python
        "region_id": listing.region_id,
        "settlement_id": listing.settlement_id,
```

`src/homealize/storage/migrations/002_enrichment.sql`:

```sql
ALTER TABLE listings ADD COLUMN region_id INTEGER;
ALTER TABLE listings ADD COLUMN settlement_id INTEGER;

CREATE INDEX idx_listings_settlement ON listings (settlement_id);

CREATE TABLE poi (
    osm_key    TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    name       TEXT,
    brand      TEXT,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE route_cache (
    lat_round   REAL NOT NULL,
    lon_round   REAL NOT NULL,
    route_km    REAL,
    route_min   REAL,
    entry       TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (lat_round, lon_round)
);

CREATE TABLE fx_rates (
    rate_date    TEXT NOT NULL,
    currency     TEXT NOT NULL,
    mdl_per_unit REAL NOT NULL,
    PRIMARY KEY (rate_date, currency)
);

CREATE TABLE scores (
    listing_id                TEXT PRIMARY KEY REFERENCES listings (id),
    computed_at               TEXT NOT NULL,
    price_eur                 REAL,
    price_per_sot_eur         REAL,
    lat                       REAL,
    lon                       REAL,
    coords_approx             INTEGER NOT NULL DEFAULT 0,
    route_km                  REAL,
    route_min                 REAL,
    nearest_entry             TEXT,
    poi_nearest_m             TEXT NOT NULL DEFAULT '{}',
    market_median_eur_per_sot REAL,
    median_level              TEXT,
    median_n                  INTEGER,
    duplicate_group           TEXT,
    is_group_primary          INTEGER NOT NULL DEFAULT 1,
    components                TEXT NOT NULL DEFAULT '{}',
    explanations              TEXT NOT NULL DEFAULT '{}',
    flags                     TEXT NOT NULL DEFAULT '[]',
    total                     REAL NOT NULL,
    excluded_reason           TEXT,
    rank                      INTEGER
);

CREATE INDEX idx_scores_rank ON scores (rank);
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 5: Commit**

```bash
git add src/homealize/models.py src/homealize/sources/nine99/parse.py src/homealize/storage/repo.py src/homealize/storage/migrations/002_enrichment.sql tests/nine99/test_parse.py tests/storage/test_db.py tests/storage/test_repo.py tests/test_cli.py
git commit -m "feat: keep 999.md region and settlement ids; schema v2 for enrichment"
```

---

### Task 3: Инфраструктура из OpenStreetMap

**Files:**
- Modify: `src/homealize/models.py` (добавить `Poi`)
- Create: `src/homealize/geo.py`, `src/homealize/storage/poi_cache.py`, `src/homealize/enrich/__init__.py`, `src/homealize/enrich/poi.py`, `tests/enrich/__init__.py`
- Test: `tests/test_geo.py`, `tests/enrich/test_poi.py`

**Interfaces:**
- Consumes: `PoiSettings`, `POI_CATEGORIES` (Task 1); таблица `poi` (Task 2); фикстура `db`.
- Produces:
  - `homealize.models.Poi(key: str, category: str, name: str | None, brand: str | None, lat: float, lon: float)` (frozen).
  - `homealize.geo.haversine_m(lat1, lon1, lat2, lon2) -> float`.
  - `homealize.storage.poi_cache`: `replace_poi(conn, pois, *, now)`, `load_poi(conn) -> list[Poi]`, `poi_fetched_at(conn) -> datetime | None`. Функции не делают commit.
  - `homealize.enrich.poi`:
    - `build_query(settings: PoiSettings) -> str`;
    - `classify(tags: dict, chains: list[str]) -> str | None`;
    - `parse_elements(payload: dict, chains: list[str]) -> list[Poi]`;
    - `fetch_pois(client: httpx.Client, settings) -> list[Poi]`;
    - `ensure_pois(conn, client, settings, *, now, force=False) -> tuple[list[Poi], str | None]`;
    - `PoiIndex(pois)` с методом `nearest_m(lat, lon) -> dict[str, int | None]` (ключи — `POI_CATEGORIES`; `None` — ближе 5 км нет).

- [ ] **Step 1: Написать падающие тесты**

`tests/enrich/__init__.py`: пустой файл.

`tests/test_geo.py`:

```python
import pytest

from homealize.geo import haversine_m


def test_one_degree_of_latitude():
    assert haversine_m(47.0, 28.8, 48.0, 28.8) == pytest.approx(111_195, rel=1e-3)


def test_same_point_is_zero():
    assert haversine_m(47.0245, 28.8327, 47.0245, 28.8327) == 0
```

`tests/enrich/test_poi.py`:

```python
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from homealize.config import PoiSettings
from homealize.enrich.poi import PoiIndex, build_query, classify, ensure_pois, parse_elements
from homealize.models import Poi
from homealize.storage import poi_cache

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
CHAINS = ["Linella", "Kaufland"]
PAYLOAD = {
    "elements": [
        {"type": "node", "id": 1, "lat": 47.0, "lon": 28.8, "tags": {"shop": "supermarket", "name": "Linella"}},
        {"type": "way", "id": 2, "center": {"lat": 47.01, "lon": 28.81}, "tags": {"amenity": "school", "name": "Liceul 1"}},
        {"type": "node", "id": 3, "lat": 47.02, "lon": 28.82, "tags": {"highway": "bus_stop"}},
        {"type": "node", "id": 4, "lat": 47.03, "lon": 28.83, "tags": {"amenity": "bank"}},
        {"type": "relation", "id": 5, "tags": {"amenity": "hospital"}},
        {"type": "node", "id": 1, "lat": 47.0, "lon": 28.8, "tags": {"shop": "supermarket", "name": "Linella"}},
    ]
}


def make_client(responses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[min(len(calls), len(responses)) - 1]

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_build_query_covers_categories_and_area():
    query = build_query(PoiSettings(chains=CHAINS))
    assert "around:35000,47.0245,28.8327" in query
    assert '"shop"="supermarket"' in query
    assert "Linella|Kaufland" in query
    assert "school|kindergarten|pharmacy|clinic|doctors|hospital" in query
    assert query.rstrip().endswith("out center tags;")


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"shop": "supermarket"}, "supermarket"),
        ({"shop": "convenience", "brand": "Linella"}, "supermarket"),
        ({"shop": "convenience", "name": "Mini Kaufland"}, "supermarket"),
        ({"shop": "convenience", "name": "Magazin"}, None),
        ({"amenity": "school"}, "school"),
        ({"amenity": "kindergarten"}, "kindergarten"),
        ({"amenity": "pharmacy"}, "pharmacy"),
        ({"amenity": "doctors"}, "clinic"),
        ({"amenity": "hospital"}, "clinic"),
        ({"highway": "bus_stop"}, "bus_stop"),
        ({"public_transport": "platform"}, "bus_stop"),
        ({"amenity": "bank"}, None),
    ],
)
def test_classify(tags, expected):
    assert classify(tags, CHAINS) == expected


def test_parse_elements_takes_coordinates_and_skips_junk():
    pois = {poi.key: poi for poi in parse_elements(PAYLOAD, CHAINS)}
    assert set(pois) == {"node/1", "way/2", "node/3"}
    assert pois["way/2"] == Poi(key="way/2", category="school", name="Liceul 1", brand=None, lat=47.01, lon=28.81)
    assert pois["node/1"].category == "supermarket"


def test_poi_index_finds_nearest_per_category():
    index = PoiIndex([
        Poi("node/1", "supermarket", None, None, 47.0036, 28.8),
        Poi("node/2", "supermarket", None, None, 47.0100, 28.8),
        Poi("node/3", "school", None, None, 47.0099, 28.8),
        Poi("node/4", "pharmacy", None, None, 47.0900, 28.8),
    ])
    nearest = index.nearest_m(47.0, 28.8)
    assert nearest["supermarket"] == pytest.approx(400, abs=2)
    assert nearest["school"] == pytest.approx(1101, abs=2)
    assert nearest["pharmacy"] is None
    assert nearest["bus_stop"] is None


def test_ensure_pois_downloads_once_and_caches(db):
    client, calls = make_client([httpx.Response(200, json=PAYLOAD)])
    settings = PoiSettings(chains=CHAINS)
    pois, warning = ensure_pois(db, client, settings, now=T0)
    assert warning is None and len(pois) == 3
    assert calls[0].method == "POST"
    pois, warning = ensure_pois(db, client, settings, now=T0 + timedelta(days=10))
    assert len(calls) == 1 and len(pois) == 3
    assert poi_cache.poi_fetched_at(db) == T0


def test_stale_pois_are_refreshed(db):
    client, calls = make_client([httpx.Response(200, json=PAYLOAD)])
    settings = PoiSettings(chains=CHAINS)
    ensure_pois(db, client, settings, now=T0)
    ensure_pois(db, client, settings, now=T0 + timedelta(days=31))
    assert len(calls) == 2


def test_failed_refresh_keeps_old_pois(db):
    client, _ = make_client([httpx.Response(200, json=PAYLOAD), httpx.Response(504)])
    settings = PoiSettings(chains=CHAINS)
    ensure_pois(db, client, settings, now=T0)
    pois, warning = ensure_pois(db, client, settings, now=T0, force=True)
    assert len(pois) == 3
    assert "POI не обновлены" in warning


def test_empty_answer_is_not_saved(db):
    client, _ = make_client([httpx.Response(200, json={"elements": []})])
    pois, warning = ensure_pois(db, client, PoiSettings(chains=CHAINS), now=T0)
    assert pois == []
    assert "0 объектов" in warning
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_geo.py tests/enrich/test_poi.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.geo'` / `'homealize.enrich'`.

- [ ] **Step 3: Реализовать**

В конец `src/homealize/models.py` добавить:

```python
@dataclass(frozen=True)
class Poi:
    """Объект инфраструктуры из OpenStreetMap (контакты организаций не храним)."""

    key: str  # "node/123", "way/456"
    category: str  # одна из config.POI_CATEGORIES
    name: str | None
    brand: str | None
    lat: float
    lon: float
```

`src/homealize/geo.py`:

```python
"""Расстояния по поверхности Земли."""

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по прямой между двумя точками, в метрах."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
```

`src/homealize/storage/poi_cache.py`:

```python
"""Объекты инфраструктуры (POI) в базе. Функции не делают commit."""

import sqlite3
from datetime import datetime

from homealize.models import Poi


def replace_poi(conn: sqlite3.Connection, pois: list[Poi], *, now: datetime) -> None:
    conn.execute("DELETE FROM poi")
    conn.executemany(
        "INSERT INTO poi (osm_key, category, name, brand, lat, lon, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(p.key, p.category, p.name, p.brand, p.lat, p.lon, now.isoformat()) for p in pois],
    )


def load_poi(conn: sqlite3.Connection) -> list[Poi]:
    return [
        Poi(key=r["osm_key"], category=r["category"], name=r["name"], brand=r["brand"], lat=r["lat"], lon=r["lon"])
        for r in conn.execute("SELECT * FROM poi ORDER BY osm_key")
    ]


def poi_fetched_at(conn: sqlite3.Connection) -> datetime | None:
    value = conn.execute("SELECT MAX(fetched_at) FROM poi").fetchone()[0]
    return datetime.fromisoformat(value) if value else None
```

`src/homealize/enrich/__init__.py`:

```python
"""Внешние данные для оценки: инфраструктура, маршруты, курс валют."""
```

`src/homealize/enrich/poi.py`:

```python
"""Инфраструктура из OpenStreetMap: запрос Overpass, классификация, поиск ближайших объектов."""

import math
import sqlite3
from datetime import datetime, timedelta

import httpx

from homealize.config import POI_CATEGORIES, PoiSettings
from homealize.geo import haversine_m
from homealize.models import Poi
from homealize.storage import poi_cache

SEARCH_RADIUS_M = 5000.0  # дальше ближайший объект не ищем: для оценки важно до 1,5 км
_METERS_PER_DEGREE_LAT = 111_320.0


def build_query(settings: PoiSettings) -> str:
    around = f"around:{round(settings.radius_km * 1000)},{settings.center_lat},{settings.center_lon}"
    chains = "|".join(settings.chains)
    return "\n".join([
        f"[out:json][timeout:{round(settings.timeout_seconds)}];",
        "(",
        f'  nwr["shop"="supermarket"]({around});',
        f'  nwr["shop"]["brand"~"{chains}",i]({around});',
        f'  nwr["shop"]["name"~"{chains}",i]({around});',
        f'  nwr["amenity"~"^(school|kindergarten|pharmacy|clinic|doctors|hospital)$"]({around});',
        f'  node["highway"="bus_stop"]({around});',
        f'  node["public_transport"="platform"]({around});',
        ");",
        "out center tags;",
    ])


def classify(tags: dict[str, str], chains: list[str]) -> str | None:
    """Категория объекта для оценки или None, если он не нужен."""
    shop = tags.get("shop")
    if shop == "supermarket" or (shop and _is_chain(tags, chains)):
        return "supermarket"
    amenity = tags.get("amenity")
    if amenity in ("school", "kindergarten", "pharmacy"):
        return amenity
    if amenity in ("clinic", "doctors", "hospital"):
        return "clinic"
    if tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform":
        return "bus_stop"
    return None


def parse_elements(payload: dict, chains: list[str]) -> list[Poi]:
    pois: dict[str, Poi] = {}
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        category = classify(tags, chains)
        centre = element.get("center") or {}
        lat = element.get("lat", centre.get("lat"))
        lon = element.get("lon", centre.get("lon"))
        if category is None or lat is None or lon is None:
            continue
        key = f"{element['type']}/{element['id']}"
        pois[key] = Poi(key=key, category=category, name=tags.get("name"), brand=tags.get("brand"),
                        lat=float(lat), lon=float(lon))
    return list(pois.values())


def fetch_pois(client: httpx.Client, settings: PoiSettings) -> list[Poi]:
    response = client.post(settings.overpass_url, data={"data": build_query(settings)},
                           timeout=settings.timeout_seconds)
    response.raise_for_status()
    return parse_elements(response.json(), settings.chains)


def ensure_pois(
    conn: sqlite3.Connection, client: httpx.Client, settings: PoiSettings, *, now: datetime, force: bool = False
) -> tuple[list[Poi], str | None]:
    """POI из базы; обновляет их из Overpass, если данных нет, они старше refresh_days или force."""
    fetched = poi_cache.poi_fetched_at(conn)
    stale = force or fetched is None or now - fetched > timedelta(days=settings.refresh_days)
    warning = None
    if stale:
        try:
            pois = fetch_pois(client, settings)
            if not pois:
                raise ValueError("Overpass вернул 0 объектов")
            poi_cache.replace_poi(conn, pois, now=now)
            conn.commit()
        except (httpx.HTTPError, ValueError) as exc:
            warning = f"POI не обновлены ({type(exc).__name__}: {exc}), используются сохранённые"
    return poi_cache.load_poi(conn), warning


class PoiIndex:
    """Поиск ближайшего объекта каждой категории."""

    def __init__(self, pois: list[Poi]) -> None:
        self._by_category: dict[str, list[Poi]] = {category: [] for category in POI_CATEGORIES}
        for poi in pois:
            self._by_category.setdefault(poi.category, []).append(poi)

    def nearest_m(self, lat: float, lon: float) -> dict[str, int | None]:
        dlat = SEARCH_RADIUS_M / _METERS_PER_DEGREE_LAT
        dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
        result: dict[str, int | None] = {}
        for category in POI_CATEGORIES:
            best = None
            for poi in self._by_category.get(category, ()):
                if abs(poi.lat - lat) > dlat or abs(poi.lon - lon) > dlon:
                    continue
                distance = haversine_m(lat, lon, poi.lat, poi.lon)
                if distance <= SEARCH_RADIUS_M and (best is None or distance < best):
                    best = distance
            result[category] = round(best) if best is not None else None
        return result


def _is_chain(tags: dict[str, str], chains: list[str]) -> bool:
    text = f"{tags.get('brand', '')} {tags.get('name', '')}".casefold()
    return any(chain.casefold() in text for chain in chains)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_geo.py tests/enrich/test_poi.py -v`
Expected: 2 + 19 passed.

Run: `uv run pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 5: Commit**

```bash
git add src/homealize/models.py src/homealize/geo.py src/homealize/storage/poi_cache.py src/homealize/enrich tests/test_geo.py tests/enrich
git commit -m "feat: infrastructure from OpenStreetMap with monthly cache"
```

---

### Task 4: Маршруты до въезда в Кишинёв (OSRM)

**Files:**
- Modify: `src/homealize/models.py` (добавить `Route`)
- Create: `src/homealize/storage/route_cache.py`, `src/homealize/enrich/routing.py`
- Test: `tests/enrich/test_routing.py`

**Interfaces:**
- Consumes: `RoutingSettings`, `EntryPoint` (Task 1); таблица `route_cache` (Task 2).
- Produces:
  - `homealize.models.Route(km: float, minutes: float, entry: str)` (frozen).
  - `homealize.storage.route_cache`: `get_routes(conn) -> dict[tuple[float, float], Route | None]`, `put_routes(conn, routes, *, now)`. Без commit.
  - `homealize.enrich.routing`:
    - `round_key(lat, lon) -> tuple[float, float]` (3 знака);
    - `RoutingError(Exception)`;
    - `OsrmRouter(client, settings)` со свойствами `batch_size`, `pause_seconds` и методом `table(points: list[tuple[float, float]]) -> dict[tuple[float, float], Route | None]`;
    - `ensure_routes(conn, router, points: set[tuple[float, float]], *, now, sleep=time.sleep) -> tuple[dict[tuple[float, float], Route | None], str | None]`.

- [ ] **Step 1: Написать падающие тесты**

`tests/enrich/test_routing.py`:

```python
from datetime import UTC, datetime

import httpx
import pytest

from homealize.config import EntryPoint, RoutingSettings
from homealize.enrich.routing import OsrmRouter, RoutingError, ensure_routes, round_key
from homealize.models import Route
from homealize.storage import route_cache

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
SETTINGS = RoutingSettings(
    entry_points=[EntryPoint(name="A", lat=47.0, lon=28.8), EntryPoint(name="B", lat=46.9, lon=28.9)],
    batch_size=2,
    pause_seconds=1.0,
)


def osrm(requests, rows):
    """rows — строки durations (секунды) по одной на каждый источник, в порядке запросов."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        n = len(request.url.params["sources"].split(";"))
        durations = [rows.pop(0) for _ in range(n)]
        distances = [[None if d is None else d * 10 for d in row] for row in durations]
        return httpx.Response(200, json={"code": "Ok", "durations": durations, "distances": distances})

    return handler


def router_with(handler) -> OsrmRouter:
    return OsrmRouter(httpx.Client(transport=httpx.MockTransport(handler)), SETTINGS)


def test_round_key():
    assert round_key(47.09921, 28.81654) == (47.099, 28.817)


def test_table_request_and_best_entry():
    requests = []
    router = router_with(osrm(requests, [[600, 300], [None, 900]]))
    routes = router.table([(46.93, 28.94), (47.25, 28.73)])
    assert routes[(46.93, 28.94)] == Route(km=3.0, minutes=5.0, entry="B")
    assert routes[(47.25, 28.73)] == Route(km=9.0, minutes=15.0, entry="B")
    url = str(requests[0].url)
    assert "/table/v1/driving/28.940000,46.930000;28.730000,47.250000;28.800000,47.000000;28.900000,46.900000" in url
    assert requests[0].url.params["sources"] == "0;1"
    assert requests[0].url.params["destinations"] == "2;3"


def test_unreachable_point_gives_none():
    router = router_with(osrm([], [[None, None]]))
    assert router.table([(46.93, 28.94)]) == {(46.93, 28.94): None}


def test_error_code_raises():
    router = router_with(lambda request: httpx.Response(200, json={"code": "InvalidQuery", "message": "bad"}))
    with pytest.raises(RoutingError, match="InvalidQuery"):
        router.table([(46.93, 28.94)])


def test_ensure_routes_batches_caches_and_pauses(db):
    requests, sleeps = [], []
    router = router_with(osrm(requests, [[60, 120], [120, 60], [600, 600]]))
    points = {(46.1, 28.1), (46.2, 28.2), (46.3, 28.3)}
    routes, warning = ensure_routes(db, router, points, now=T0, sleep=sleeps.append)
    assert warning is None
    assert len(requests) == 2 and sleeps == [1.0]
    assert routes[(46.1, 28.1)].entry == "A"
    assert routes[(46.2, 28.2)].entry == "B"
    again, _ = ensure_routes(db, router, points, now=T0, sleep=sleeps.append)
    assert again == routes and len(requests) == 2
    assert len(route_cache.get_routes(db)) == 3


def test_failure_keeps_finished_batches(db):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(502)
        return httpx.Response(200, json={"code": "Ok", "durations": [[60, 120], [60, 120]],
                                          "distances": [[600, 1200], [600, 1200]]})

    router = router_with(handler)
    points = {(46.1, 28.1), (46.2, 28.2), (46.3, 28.3)}
    routes, warning = ensure_routes(db, router, points, now=T0, sleep=lambda seconds: None)
    assert len(routes) == 2
    assert "Маршруты не посчитаны для 1 точек" in warning
    assert len(route_cache.get_routes(db)) == 2
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/enrich/test_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.enrich.routing'`.

- [ ] **Step 3: Реализовать**

В конец `src/homealize/models.py` добавить:

```python
@dataclass(frozen=True)
class Route:
    """Путь на машине до ближайшей (по времени) точки въезда в Кишинёв."""

    km: float
    minutes: float
    entry: str  # название точки въезда
```

`src/homealize/storage/route_cache.py`:

```python
"""Кэш маршрутов OSRM по округлённым координатам. Функции не делают commit."""

import sqlite3
from datetime import datetime

from homealize.models import Route


def get_routes(conn: sqlite3.Connection) -> dict[tuple[float, float], Route | None]:
    """Все посчитанные точки; None — OSRM не нашёл дороги."""
    routes: dict[tuple[float, float], Route | None] = {}
    for row in conn.execute("SELECT lat_round, lon_round, route_km, route_min, entry FROM route_cache"):
        key = (row["lat_round"], row["lon_round"])
        routes[key] = (
            Route(km=row["route_km"], minutes=row["route_min"], entry=row["entry"])
            if row["route_km"] is not None
            else None
        )
    return routes


def put_routes(conn: sqlite3.Connection, routes: dict[tuple[float, float], Route | None], *, now: datetime) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO route_cache (lat_round, lon_round, route_km, route_min, entry, computed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (key[0], key[1], route.km if route else None, route.minutes if route else None,
             route.entry if route else None, now.isoformat())
            for key, route in routes.items()
        ],
    )
```

`src/homealize/enrich/routing.py`:

```python
"""Маршруты OSRM до ближайшей точки въезда в Кишинёв, с кэшем в базе."""

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from homealize.config import RoutingSettings
from homealize.models import Route
from homealize.storage import route_cache


class RoutingError(Exception):
    """OSRM ответил кодом ошибки."""


def round_key(lat: float, lon: float) -> tuple[float, float]:
    """Ключ кэша: координаты с точностью ~100 м."""
    return (round(lat, 3), round(lon, 3))


class OsrmRouter:
    def __init__(self, client: httpx.Client, settings: RoutingSettings) -> None:
        self._client = client
        self._settings = settings

    @property
    def batch_size(self) -> int:
        return self._settings.batch_size

    @property
    def pause_seconds(self) -> float:
        return self._settings.pause_seconds

    def table(self, points: list[tuple[float, float]]) -> dict[tuple[float, float], Route | None]:
        """Один табличный запрос: от каждой точки (lat, lon) до всех точек въезда; берётся самая быстрая."""
        entries = self._settings.entry_points
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
        coords += ";" + ";".join(f"{entry.lon:.6f},{entry.lat:.6f}" for entry in entries)
        n = len(points)
        sources = ";".join(str(i) for i in range(n))
        destinations = ";".join(str(n + j) for j in range(len(entries)))
        url = (
            f"{self._settings.osrm_url}/table/v1/driving/{coords}"
            f"?sources={sources}&destinations={destinations}&annotations=duration,distance"
        )
        response = self._client.get(url, timeout=self._settings.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "Ok":
            raise RoutingError(f"OSRM: {data.get('code')} {data.get('message', '')}".strip())
        routes: dict[tuple[float, float], Route | None] = {}
        for point, durations, distances in zip(points, data["durations"], data["distances"]):
            best = None
            for entry, duration, distance in zip(entries, durations, distances):
                if duration is None or distance is None:
                    continue
                if best is None or duration < best[0]:
                    best = (duration, distance, entry.name)
            routes[point] = (
                Route(km=round(best[1] / 1000, 2), minutes=round(best[0] / 60, 1), entry=best[2]) if best else None
            )
        return routes


def ensure_routes(
    conn: sqlite3.Connection,
    router: OsrmRouter,
    points: set[tuple[float, float]],
    *,
    now: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[tuple[float, float], Route | None], str | None]:
    """Маршруты для точек (уже округлённых round_key): из кэша, недостающие — пачками из OSRM."""
    cached = route_cache.get_routes(conn)
    missing = sorted(point for point in points if point not in cached)
    warning = None
    for start in range(0, len(missing), router.batch_size):
        if start:
            sleep(router.pause_seconds)
        batch = missing[start:start + router.batch_size]
        try:
            fresh = router.table(batch)
        except (httpx.HTTPError, RoutingError, ValueError, KeyError) as exc:
            warning = f"Маршруты не посчитаны для {len(missing) - start} точек ({type(exc).__name__}: {exc})"
            break
        route_cache.put_routes(conn, fresh, now=now)
        conn.commit()
        cached.update(fresh)
    return {point: cached[point] for point in points if point in cached}, warning
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/enrich/test_routing.py -v`
Expected: 6 passed.

Run: `uv run pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 5: Commit**

```bash
git add src/homealize/models.py src/homealize/storage/route_cache.py src/homealize/enrich/routing.py tests/enrich/test_routing.py
git commit -m "feat: OSRM routes to the nearest Chisinau entry with a cache"
```

---

### Task 5: Курс Нацбанка Молдовы

**Files:**
- Create: `src/homealize/storage/rate_cache.py`, `src/homealize/enrich/currency.py`
- Test: `tests/enrich/test_currency.py`

**Interfaces:**
- Consumes: `CurrencySettings` (Task 1); таблица `fx_rates` (Task 2).
- Produces:
  - `homealize.storage.rate_cache`: `get_rates(conn, day: str) -> dict[str, float] | None`, `put_rates(conn, day: str, rates)`, `latest_rates(conn) -> tuple[str, dict[str, float]] | None`. Без commit.
  - `homealize.enrich.currency`:
    - `fetch_bnm_rates(client, settings, day: date) -> dict[str, float]` — леев за единицу валюты, `"MDL": 1.0`;
    - `ensure_rates(conn, client, settings, *, day: date) -> tuple[dict[str, float] | None, str | None]`;
    - `to_eur(price: float | None, currency: str | None, rates: dict[str, float] | None) -> float | None`.

- [ ] **Step 1: Написать падающие тесты**

`tests/enrich/test_currency.py`:

```python
from datetime import date

import httpx
import pytest

from homealize.config import CurrencySettings
from homealize.enrich.currency import ensure_rates, fetch_bnm_rates, to_eur

D1 = date(2026, 10, 29)
BNM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ValCurs Date="29.10.2026" name="Official exchange rate">
  <Valute ID="47"><NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal><Name>Euro</Name><Value>20.1303</Value></Valute>
  <Valute ID="44"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>US Dollar</Name><Value>18.0881</Value></Valute>
  <Valute ID="1"><NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal><Name>Yen</Name><Value>11.80</Value></Valute>
</ValCurs>"""


def bnm_client(responses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[min(len(calls), len(responses)) - 1]

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_fetch_parses_rates_and_nominal():
    client, calls = bnm_client([httpx.Response(200, text=BNM_XML)])
    rates = fetch_bnm_rates(client, CurrencySettings(), D1)
    assert rates["EUR"] == 20.1303 and rates["USD"] == 18.0881 and rates["MDL"] == 1.0
    assert rates["JPY"] == pytest.approx(0.118)
    assert calls[0].url.params["date"] == "29.10.2026"
    assert calls[0].url.params["get_xml"] == "1"


def test_answer_without_eur_is_an_error():
    client, _ = bnm_client([httpx.Response(200, text="<ValCurs></ValCurs>")])
    with pytest.raises(ValueError, match="EUR"):
        fetch_bnm_rates(client, CurrencySettings(), D1)


@pytest.mark.parametrize(
    ("price", "currency", "rates", "expected"),
    [
        (1000.0, "EUR", None, 1000.0),
        (201303.0, "MDL", {"EUR": 20.1303, "MDL": 1.0}, 10000.0),
        (1000.0, "USD", {"EUR": 20.1303, "USD": 18.0881, "MDL": 1.0}, 898.55),
        (1000.0, "GBP", {"EUR": 20.1303, "MDL": 1.0}, None),
        (1000.0, "MDL", None, None),
        (None, "EUR", None, None),
        (1000.0, None, {"EUR": 20.1303}, None),
    ],
)
def test_to_eur(price, currency, rates, expected):
    assert to_eur(price, currency, rates) == expected


def test_rates_are_cached_per_day(db):
    client, calls = bnm_client([httpx.Response(200, text=BNM_XML)])
    first, warning1 = ensure_rates(db, client, CurrencySettings(), day=D1)
    second, warning2 = ensure_rates(db, client, CurrencySettings(), day=D1)
    assert first == second and first["EUR"] == 20.1303
    assert (warning1, warning2) == (None, None)
    assert len(calls) == 1


def test_failure_uses_latest_cached_day(db):
    client, _ = bnm_client([httpx.Response(200, text=BNM_XML), httpx.Response(500)])
    ensure_rates(db, client, CurrencySettings(), day=D1)
    rates, warning = ensure_rates(db, client, CurrencySettings(), day=date(2026, 10, 30))
    assert rates["EUR"] == 20.1303
    assert "используется курс от 2026-10-29" in warning


def test_failure_without_cache(db):
    client, _ = bnm_client([httpx.Response(500)])
    rates, warning = ensure_rates(db, client, CurrencySettings(), day=D1)
    assert rates is None
    assert "не пересчитаны" in warning
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/enrich/test_currency.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.enrich.currency'`.

- [ ] **Step 3: Реализовать**

`src/homealize/storage/rate_cache.py`:

```python
"""Курсы валют BNM по дням (леев за единицу валюты). Функции не делают commit."""

import sqlite3


def get_rates(conn: sqlite3.Connection, day: str) -> dict[str, float] | None:
    rows = conn.execute("SELECT currency, mdl_per_unit FROM fx_rates WHERE rate_date = ?", (day,)).fetchall()
    return {row["currency"]: row["mdl_per_unit"] for row in rows} or None


def put_rates(conn: sqlite3.Connection, day: str, rates: dict[str, float]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO fx_rates (rate_date, currency, mdl_per_unit) VALUES (?, ?, ?)",
        [(day, currency, value) for currency, value in rates.items()],
    )


def latest_rates(conn: sqlite3.Connection) -> tuple[str, dict[str, float]] | None:
    day = conn.execute("SELECT MAX(rate_date) FROM fx_rates").fetchone()[0]
    if day is None:
        return None
    return day, get_rates(conn, day) or {}
```

`src/homealize/enrich/currency.py`:

```python
"""Официальный курс Нацбанка Молдовы (BNM) и перевод цен в евро."""

import sqlite3
from datetime import date
from xml.etree import ElementTree

import httpx

from homealize.config import CurrencySettings
from homealize.storage import rate_cache


def fetch_bnm_rates(client: httpx.Client, settings: CurrencySettings, day: date) -> dict[str, float]:
    """Курсы на день: сколько леев стоит единица валюты."""
    response = client.get(settings.bnm_url, params={"get_xml": "1", "date": day.strftime("%d.%m.%Y")},
                          timeout=settings.timeout_seconds)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    rates = {"MDL": 1.0}
    for valute in root.iter("Valute"):
        code = valute.findtext("CharCode")
        value = valute.findtext("Value")
        nominal = valute.findtext("Nominal") or "1"
        if code and value:
            rates[code] = float(value) / float(nominal)
    if "EUR" not in rates:
        raise ValueError("в ответе BNM нет курса EUR")
    return rates


def ensure_rates(
    conn: sqlite3.Connection, client: httpx.Client, settings: CurrencySettings, *, day: date
) -> tuple[dict[str, float] | None, str | None]:
    """Курс на день из кэша или BNM; при сбое — последний сохранённый."""
    key = day.isoformat()
    cached = rate_cache.get_rates(conn, key)
    if cached:
        return cached, None
    try:
        rates = fetch_bnm_rates(client, settings, day)
    except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
        message = f"Курс BNM не получен ({type(exc).__name__}: {exc})"
        latest = rate_cache.latest_rates(conn)
        if latest:
            return latest[1], f"{message}, используется курс от {latest[0]}"
        return None, f"{message}, цены не в евро не пересчитаны"
    rate_cache.put_rates(conn, key, rates)
    conn.commit()
    return rates, None


def to_eur(price: float | None, currency: str | None, rates: dict[str, float] | None) -> float | None:
    if price is None or currency is None:
        return None
    if currency == "EUR":
        return price
    if not rates or currency not in rates or "EUR" not in rates:
        return None
    return round(price * rates[currency] / rates["EUR"], 2)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/enrich/test_currency.py -v`
Expected: 12 passed.

Run: `uv run pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 5: Commit**

```bash
git add src/homealize/storage/rate_cache.py src/homealize/enrich/currency.py tests/enrich/test_currency.py
git commit -m "feat: BNM exchange rates with daily cache"
```

---

### Task 6: Рынок — медианы, «подозрительно дёшево», дубликаты

**Files:**
- Create: `src/homealize/scoring/__init__.py`, `src/homealize/scoring/market.py`, `tests/scoring/__init__.py`
- Test: `tests/scoring/test_market.py`

**Interfaces:**
- Consumes: `haversine_m` (Task 3).
- Produces (`homealize.scoring.market`):
  - `MarketItem(listing_id: str, settlement_id: int | None, route_km: float | None, price_per_sot: float)`;
  - `Median(value: float, level: str, n: int, label: str)`, где `level` — одно из `"settlement"`, `"route_band"`, `"region"`;
  - `MarketMedians(items, *, min_settlement, min_route_band, band_km, settlement_names: dict[int, str])` с методом `for_listing(settlement_id, route_km) -> Median | None`;
  - `is_suspicious(price_per_sot, median, ratio) -> bool`;
  - `DupItem(listing_id, area_sot, lat, lon, price_eur)`;
  - `group_duplicates(items, *, distance_m, price_tolerance) -> dict[str, tuple[str, bool]]` — id → (id группы, главное ли). В результат попадают только группы из 2 и больше объявлений.

- [ ] **Step 1: Написать падающие тесты**

`tests/scoring/__init__.py`: пустой файл.

`tests/scoring/test_market.py`:

```python
from homealize.scoring.market import (
    DupItem,
    MarketItem,
    MarketMedians,
    Median,
    group_duplicates,
    is_suspicious,
)

LEVELS = {"min_settlement": 5, "min_route_band": 8, "band_km": 3}


def test_settlement_level_needs_five():
    items = [MarketItem(f"s{i}", 1, 10.0, value) for i, value in enumerate([4000, 5000, 6000, 7000, 8000])]
    medians = MarketMedians(items, settlement_names={1: "Сынжера"}, **LEVELS)
    assert medians.for_listing(1, 10.0) == Median(value=6000, level="settlement", n=5, label="село Сынжера")


def test_falls_back_to_route_band():
    near = [MarketItem(f"n{i}", 2, 10.0 + i * 0.3, 3000 + i * 100) for i in range(8)]
    far = [MarketItem(f"f{i}", 3, 25.0, 1000) for i in range(3)]
    medians = MarketMedians(near + far, settlement_names={}, **LEVELS)
    median = medians.for_listing(9, 11.0)
    assert (median.level, median.n, median.value) == ("route_band", 8, 3350)
    assert median.label == "участки в 8–14 км от города"


def test_falls_back_to_region():
    medians = MarketMedians([MarketItem("a", 1, 10.0, 1000), MarketItem("b", 2, 20.0, 3000)],
                            settlement_names={}, **LEVELS)
    median = medians.for_listing(5, None)
    assert (median.level, median.n, median.value, median.label) == ("region", 2, 2000, "весь регион")


def test_empty_market_gives_none():
    assert MarketMedians([], settlement_names={}, **LEVELS).for_listing(1, 5.0) is None


def test_is_suspicious():
    median = Median(4000, "settlement", 5, "x")
    assert is_suspicious(999.0, median, 0.25)
    assert not is_suspicious(1000.0, median, 0.25)
    assert not is_suspicious(None, median, 0.25)
    assert not is_suspicious(500.0, None, 0.25)


def test_group_duplicates():
    items = [
        DupItem("a", 6.0, 46.9300, 28.9400, 30000.0),
        DupItem("b", 6.0, 46.9302, 28.9401, 31000.0),
        DupItem("c", 6.0, 46.9400, 28.9400, 30000.0),
        DupItem("d", 7.0, 46.9300, 28.9400, 30000.0),
        DupItem("e", 6.0, 46.9301, 28.9400, 40000.0),
    ]
    assert group_duplicates(items, distance_m=50, price_tolerance=0.10) == {"a": ("a", True), "b": ("a", False)}


def test_cheapest_listing_is_primary():
    items = [DupItem("a", 6.0, 46.93, 28.94, 30000.0), DupItem("b", 6.0, 46.93, 28.94, 29000.0)]
    assert group_duplicates(items, distance_m=50, price_tolerance=0.10) == {"a": ("a", False), "b": ("a", True)}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/scoring/test_market.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.scoring'`.

- [ ] **Step 3: Реализовать**

`src/homealize/scoring/__init__.py`:

```python
"""Оценка участков по «весам» спеки (§6)."""
```

`src/homealize/scoring/market.py`:

```python
"""Рынок: медианы €/сотку по уровням, «подозрительно дёшево», группы дубликатов (спека §5, §6.1)."""

from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from homealize.geo import haversine_m


@dataclass(frozen=True)
class MarketItem:
    listing_id: str
    settlement_id: int | None
    route_km: float | None
    price_per_sot: float


@dataclass(frozen=True)
class Median:
    value: float
    level: str  # settlement | route_band | region
    n: int
    label: str  # для пояснения: «село Сынжера», «участки в 8–14 км от города», «весь регион»


class MarketMedians:
    """Медиана по селу (n ≥ min_settlement), иначе по участкам ±band_km (n ≥ min_route_band), иначе по региону."""

    def __init__(
        self,
        items: list[MarketItem],
        *,
        min_settlement: int,
        min_route_band: int,
        band_km: float,
        settlement_names: dict[int, str],
    ) -> None:
        self._items = list(items)
        self._min_settlement = min_settlement
        self._min_route_band = min_route_band
        self._band_km = band_km
        self._names = settlement_names
        self._by_settlement: dict[int, list[float]] = defaultdict(list)
        for item in self._items:
            if item.settlement_id is not None:
                self._by_settlement[item.settlement_id].append(item.price_per_sot)

    def for_listing(self, settlement_id: int | None, route_km: float | None) -> Median | None:
        values = self._by_settlement.get(settlement_id, []) if settlement_id is not None else []
        if len(values) >= self._min_settlement:
            name = self._names.get(settlement_id, str(settlement_id))
            return Median(round(median(values), 2), "settlement", len(values), f"село {name}")
        if route_km is not None:
            band = [
                item.price_per_sot
                for item in self._items
                if item.route_km is not None and abs(item.route_km - route_km) <= self._band_km
            ]
            if len(band) >= self._min_route_band:
                low, high = max(0.0, route_km - self._band_km), route_km + self._band_km
                return Median(round(median(band), 2), "route_band", len(band),
                              f"участки в {low:.0f}–{high:.0f} км от города")
        if self._items:
            return Median(round(median(item.price_per_sot for item in self._items), 2), "region",
                          len(self._items), "весь регион")
        return None


def is_suspicious(price_per_sot: float | None, market: Median | None, ratio: float) -> bool:
    return price_per_sot is not None and market is not None and price_per_sot < ratio * market.value


@dataclass(frozen=True)
class DupItem:
    listing_id: str
    area_sot: float
    lat: float
    lon: float
    price_eur: float


def group_duplicates(
    items: list[DupItem], *, distance_m: float, price_tolerance: float
) -> dict[str, tuple[str, bool]]:
    """Одинаковая площадь, ≤ distance_m и цена в пределах price_tolerance — одна группа.

    Возвращает id → (id группы = наименьший id, главное ли объявление = самое дешёвое).
    """
    parent = {item.listing_id: item.listing_id for item in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_area: dict[float, list[DupItem]] = defaultdict(list)
    for item in items:
        by_area[item.area_sot].append(item)
    for group in by_area.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if haversine_m(a.lat, a.lon, b.lat, b.lon) > distance_m:
                    continue
                low = min(a.price_eur, b.price_eur)
                if low <= 0 or abs(a.price_eur - b.price_eur) / low > price_tolerance:
                    continue
                root_a, root_b = find(a.listing_id), find(b.listing_id)
                if root_a != root_b:
                    parent[max(root_a, root_b)] = min(root_a, root_b)

    members: dict[str, list[DupItem]] = defaultdict(list)
    for item in items:
        members[find(item.listing_id)].append(item)
    result: dict[str, tuple[str, bool]] = {}
    for group in members.values():
        if len(group) < 2:
            continue
        primary = min(group, key=lambda item: (item.price_eur, item.listing_id))
        group_id = min(item.listing_id for item in group)
        for item in group:
            result[item.listing_id] = (group_id, item.listing_id == primary.listing_id)
    return result
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/scoring/test_market.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/homealize/scoring/__init__.py src/homealize/scoring/market.py tests/scoring/__init__.py tests/scoring/test_market.py
git commit -m "feat: market medians, suspicious prices and duplicate groups"
```

---

### Task 7: Компоненты балла и пояснения

**Files:**
- Create: `src/homealize/scoring/components.py`
- Test: `tests/scoring/test_components.py`

**Interfaces:**
- Consumes: `ScoringWeights` (Task 1), `SettlementRef` (Task 1), `Route` (Task 4).
- Produces (`homealize.scoring.components`):
  - `IN_CITY_ENTRY = "в черте города"`; словари `CATEGORY_LABELS`, `FLAG_LABELS`, `EXCLUSION_LABELS`.
  - `Component(score: float, note: str)` (frozen).
  - `fraction(value, best, worst) -> float`.
  - Компоненты:
    - `price_component(price_per_sot, median, *, label, n, full_discount, suspicious) -> Component`;
    - `route_component(route, *, best_min, worst_min) -> Component`;
    - `infra_component(nearest: dict[str, float | None], *, weights, full_m, zero_m) -> Component`;
    - `utilities_component(values: dict[str, object], *, weights) -> tuple[Component, list[str]]`;
    - `settlement_component(ref, *, unknown_score) -> tuple[Component, list[str]]`.
  - `weighted_total(components: dict[str, Component], weights: ScoringWeights) -> float` (1 знак). Ключи словаря: `price`, `route`, `infrastructure`, `utilities`, `settlement`.
  - Коды меток: `suspicious_price`, `coords_approx`, `no_coords`, `settlement_unknown`, `no_electricity`, `no_gas`, `no_water`, `no_sewerage`.
  - Коды исключений: `no_coords`, `no_route`, `too_far`, `no_price_eur`, `over_budget`, `suspicious_price`, `duplicate`.

- [ ] **Step 1: Написать падающие тесты**

`tests/scoring/test_components.py`:

```python
import pytest

from homealize.config import ScoringWeights
from homealize.models import Route
from homealize.scoring.components import (
    EXCLUSION_LABELS,
    FLAG_LABELS,
    IN_CITY_ENTRY,
    Component,
    fraction,
    infra_component,
    price_component,
    route_component,
    settlement_component,
    utilities_component,
    weighted_total,
)
from homealize.settlements import SettlementRef

UTILITY_WEIGHTS = {"electricity": 35, "gas": 30, "water": 25, "sewerage": 10}
POI_WEIGHTS = {"supermarket": 0.30, "school": 0.20, "kindergarten": 0.15, "pharmacy": 0.15, "bus_stop": 0.10,
               "clinic": 0.10}


def test_fraction():
    assert fraction(380, 500, 1500) == 1.0
    assert fraction(900, 500, 1500) == pytest.approx(0.6)
    assert fraction(11, 5, 30) == pytest.approx(0.76)
    assert fraction(40, 5, 30) == 0.0


def test_price_component_cheaper_than_market():
    c = price_component(3150.0, 4200.0, label="село Сынжера", n=11, full_discount=0.4, suspicious=False)
    assert c.score == pytest.approx(81.25)
    assert c.note == "Цена 81: на 25% дешевле медианы (4200 €/сот., село Сынжера, объявлений: 11)"


def test_price_component_expensive_is_clamped():
    c = price_component(8400.0, 4200.0, label="весь регион", n=3, full_discount=0.4, suspicious=False)
    assert c.score == 0.0
    assert "на 100% дороже медианы" in c.note


def test_price_component_at_median():
    c = price_component(4200.0, 4200.0, label="весь регион", n=3, full_discount=0.4, suspicious=False)
    assert c.score == 50.0 and "на уровне медианы" in c.note


def test_price_component_suspicious():
    c = price_component(500.0, 4200.0, label="весь регион", n=3, full_discount=0.4, suspicious=True)
    assert c.score == 50.0 and "подозрительно дёшево" in c.note


def test_price_component_without_market():
    c = price_component(4200.0, None, label="", n=0, full_discount=0.4, suspicious=False)
    assert (c.score, c.note) == (50.0, "Цена 50: не с чем сравнить")


def test_route_component():
    c = route_component(Route(km=9.4, minutes=11.0, entry="бул. Дачия (юг)"), best_min=5, worst_min=30)
    assert c.score == pytest.approx(76.0)
    assert c.note == "Дорога 76: 11 мин до въезда «бул. Дачия (юг)» (9.4 км)"


def test_route_component_in_city_and_missing():
    city = route_component(Route(km=0.0, minutes=0.0, entry=IN_CITY_ENTRY), best_min=5, worst_min=30)
    assert (city.score, city.note) == (100.0, "Дорога 100: участок в черте Кишинёва")
    missing = route_component(None, best_min=5, worst_min=30)
    assert missing.score == 0.0 and "не найден" in missing.note


def test_infra_component():
    nearest = {"supermarket": 380, "school": 900, "kindergarten": None, "pharmacy": 450, "bus_stop": 2000,
               "clinic": 1500}
    c = infra_component(nearest, weights=POI_WEIGHTS, full_m=500, zero_m=1500)
    assert c.score == pytest.approx(57.0)
    assert c.note == (
        "Инфраструктура 57: супермаркет 380 м, школа 900 м, детсад нет ближе 1,5 км, аптека 450 м, "
        "остановка нет ближе 1,5 км, медпункт нет ближе 1,5 км"
    )


def test_utilities_component():
    c, flags = utilities_component({"electricity": 1, "gas": 1, "water": 1, "sewerage": 0}, weights=UTILITY_WEIGHTS)
    assert c.score == 90.0
    assert flags == ["no_sewerage"]
    assert c.note == "Коммуникации 90: есть свет, газ, вода; нет канализации"


def test_utilities_component_nothing_marked():
    c, flags = utilities_component({"electricity": None, "gas": None, "water": None, "sewerage": None},
                                   weights=UTILITY_WEIGHTS)
    assert c.score == 0.0
    assert flags == ["no_electricity", "no_gas", "no_water", "no_sewerage"]
    assert c.note == "Коммуникации 0: ничего не указано; нет электричества, газа, воды, канализации"


def test_settlement_component():
    known, flags = settlement_component(SettlementRef(id=1, name="Сынжера", potential="Высокий", score=85),
                                        unknown_score=50)
    assert (known.score, known.note, flags) == (85, "Село 85: «Высокий» по справочнику", [])
    unknown, flags = settlement_component(None, unknown_score=50)
    assert unknown.score == 50 and flags == ["settlement_unknown"]


def test_weighted_total():
    components = {name: Component(score, "") for name, score in
                  {"price": 80, "route": 76, "infrastructure": 57, "utilities": 90, "settlement": 85}.items()}
    assert weighted_total(components, ScoringWeights()) == 77.1


def test_every_flag_and_exclusion_has_a_label():
    for flag in ["suspicious_price", "coords_approx", "no_coords", "settlement_unknown", "no_electricity",
                 "no_gas", "no_water", "no_sewerage"]:
        assert flag in FLAG_LABELS
    for reason in ["no_coords", "no_route", "too_far", "no_price_eur", "over_budget", "suspicious_price",
                   "duplicate"]:
        assert reason in EXCLUSION_LABELS
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/scoring/test_components.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.scoring.components'`.

- [ ] **Step 3: Реализовать `src/homealize/scoring/components.py`**

```python
"""Компоненты балла участка (спека §6): каждый даёт 0–100 и короткое пояснение по-русски."""

from dataclasses import dataclass

from homealize.config import ScoringWeights
from homealize.models import Route
from homealize.settlements import SettlementRef

IN_CITY_ENTRY = "в черте города"

CATEGORY_LABELS = {
    "supermarket": "супермаркет",
    "school": "школа",
    "kindergarten": "детсад",
    "pharmacy": "аптека",
    "bus_stop": "остановка",
    "clinic": "медпункт",
}
UTILITY_NAMES = {"electricity": "свет", "gas": "газ", "water": "вода", "sewerage": "канализация"}
UTILITY_MISSING = {"electricity": "электричества", "gas": "газа", "water": "воды", "sewerage": "канализации"}

FLAG_LABELS = {
    "suspicious_price": "🔍 подозрительно дёшево — проверить вручную",
    "coords_approx": "📍 координаты приблизительные (центр села)",
    "no_coords": "📍 нет координат",
    "settlement_unknown": "село не изучено",
    **{f"no_{utility}": f"⚠ нет {name} (не указано в объявлении)" for utility, name in UTILITY_MISSING.items()},
}
EXCLUSION_LABELS = {
    "no_coords": "нет координат",
    "no_route": "маршрут до города не найден",
    "too_far": "слишком далеко от города",
    "no_price_eur": "цену не удалось перевести в евро",
    "over_budget": "дороже бюджета",
    "suspicious_price": "подозрительно дёшево",
    "duplicate": "дубликат более дешёвого объявления",
}


@dataclass(frozen=True)
class Component:
    score: float
    note: str


def fraction(value: float, best: float, worst: float) -> float:
    """1 — значение на уровне best или лучше, 0 — на уровне worst или хуже, между ними линейно."""
    t = (value - worst) / (best - worst)
    return min(1.0, max(0.0, t))


def price_component(
    price_per_sot: float | None,
    median: float | None,
    *,
    label: str,
    n: int,
    full_discount: float,
    suspicious: bool,
) -> Component:
    if suspicious:
        return Component(
            50.0,
            f"Цена 50: подозрительно дёшево — {price_per_sot:.0f} €/сот. при медиане {median:.0f} €/сот., "
            "проверьте вручную",
        )
    if price_per_sot is None or not median:
        return Component(50.0, "Цена 50: не с чем сравнить")
    deviation = (price_per_sot - median) / median
    score = min(100.0, max(0.0, 50.0 - 50.0 * deviation / full_discount))
    percent = round(abs(deviation) * 100)
    if percent == 0:
        comparison = "на уровне медианы"
    else:
        comparison = f"на {percent}% {'дешевле' if deviation < 0 else 'дороже'} медианы"
    return Component(score, f"Цена {score:.0f}: {comparison} ({median:.0f} €/сот., {label}, объявлений: {n})")


def route_component(route: Route | None, *, best_min: float, worst_min: float) -> Component:
    if route is None:
        return Component(0.0, "Дорога 0: маршрут до города не найден")
    if route.entry == IN_CITY_ENTRY:
        return Component(100.0, "Дорога 100: участок в черте Кишинёва")
    score = 100.0 * fraction(route.minutes, best_min, worst_min)
    return Component(score, f"Дорога {score:.0f}: {route.minutes:.0f} мин до въезда «{route.entry}» ({route.km:.1f} км)")


def infra_component(
    nearest: dict[str, float | None], *, weights: dict[str, float], full_m: float, zero_m: float
) -> Component:
    total_weight = sum(weights.values())
    earned = 0.0
    parts = []
    for category, weight in weights.items():
        distance = nearest.get(category)
        label = CATEGORY_LABELS[category]
        if distance is not None and distance < zero_m:
            earned += weight * fraction(distance, full_m, zero_m)
            parts.append(f"{label} {distance:.0f} м")
        else:
            parts.append(f"{label} нет ближе {_km(zero_m)} км")
    score = 100.0 * earned / total_weight if total_weight else 0.0
    return Component(score, f"Инфраструктура {score:.0f}: " + ", ".join(parts))


def utilities_component(values: dict[str, object], *, weights: dict[str, float]) -> tuple[Component, list[str]]:
    present = [utility for utility in weights if values.get(utility)]
    missing = [utility for utility in weights if not values.get(utility)]
    total = sum(weights.values())
    score = 100.0 * sum(weights[utility] for utility in present) / total if total else 0.0
    note = f"Коммуникации {score:.0f}: "
    note += f"есть {', '.join(UTILITY_NAMES[u] for u in present)}" if present else "ничего не указано"
    if missing:
        note += f"; нет {', '.join(UTILITY_MISSING[u] for u in missing)}"
    return Component(score, note), [f"no_{utility}" for utility in missing]


def settlement_component(ref: SettlementRef | None, *, unknown_score: float) -> tuple[Component, list[str]]:
    if ref is None:
        return Component(unknown_score, f"Село {unknown_score:.0f}: нет в справочнике, не изучено"), [
            "settlement_unknown"
        ]
    return Component(ref.score, f"Село {ref.score:.0f}: «{ref.potential}» по справочнику"), []


def weighted_total(components: dict[str, Component], weights: ScoringWeights) -> float:
    return round(
        components["price"].score * weights.price
        + components["route"].score * weights.route
        + components["infrastructure"].score * weights.infrastructure
        + components["utilities"].score * weights.utilities
        + components["settlement"].score * weights.settlement,
        1,
    )


def _km(meters: float) -> str:
    return f"{meters / 1000:g}".replace(".", ",")
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/scoring/test_components.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/homealize/scoring/components.py tests/scoring/test_components.py
git commit -m "feat: score components with Russian explanations"
```

---

### Task 8: Движок оценки и таблица `scores`

**Files:**
- Create: `src/homealize/storage/scores.py`, `src/homealize/scoring/engine.py`
- Test: `tests/scoring/test_engine.py`

**Interfaces:**
- Consumes:
  - `Settings` (Task 1), `SettlementRef` (Task 1);
  - `PoiIndex` (Task 3), `round_key` (Task 4), `to_eur` (Task 5), `Route` (Task 4);
  - `market.*` (Task 6), `components.*` (Task 7);
  - `repo.upsert_listing` и `make_listing` из этапа 1 (`make_listing` теперь принимает `settlement_id`).
- Produces:
  - `homealize.storage.scores`:
    - `SCORE_COLUMNS: tuple[str, ...]`;
    - `replace_scores(conn, records: list[dict])` — удаляет старый снимок и пишет новый, без commit;
    - `load_scored(conn) -> list[sqlite3.Row]` — `scores` вместе с полями `listings` (`title, url, region, settlement, zone, area_sot, price_original, currency, author_type, first_seen_at`); порядок: сначала по месту, потом остальные по баллу.
  - `homealize.scoring.engine`:
    - `Location(lat, lon, approx)`;
    - `Services(routes: dict[tuple[float, float], Route | None], poi_index: PoiIndex, rates: dict[str, float] | None, settlements: dict[int, SettlementRef])`;
    - `ScoreSummary(scored: int, eligible: int, best_total: float | None, warnings: list[str])`;
    - `load_rows(conn, settings, *, now) -> list[sqlite3.Row]`;
    - `locate(rows) -> dict[str, Location | None]`;
    - `route_points(conn, settings, *, now) -> set[tuple[float, float]]`;
    - `rescore(conn, settings, services, *, now) -> ScoreSummary` — не делает commit и не ходит в сеть.

- [ ] **Step 1: Написать падающие тесты**

`tests/scoring/test_engine.py`:

```python
import json
from datetime import UTC, datetime, timedelta

import pytest

from homealize.config import Settings
from homealize.enrich.poi import PoiIndex
from homealize.enrich.routing import round_key
from homealize.models import Poi, Route
from homealize.scoring.components import IN_CITY_ENTRY
from homealize.scoring.engine import Services, rescore, route_points
from homealize.settlements import SettlementRef
from homealize.storage import repo
from tests.factories import make_listing

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
SETTINGS = Settings()
SINGERA, CITY = 14398, 13859
NEAR = Route(km=8.0, minutes=10.0, entry="M3")


class AnyRoute(dict):
    """Маршрут по умолчанию для любой точки; явно заданные ключи (в том числе None) важнее."""

    def __init__(self, default):
        super().__init__()
        self.default = default

    def get(self, key, default=None):
        return super().get(key, self.default)


def add(db, lid, pps, *, area=6.0, lat=46.930, lon=28.940, settlement_id=SINGERA, settlement="Сынжера",
        status="active", **extra):
    listing = make_listing(id=lid, url=f"https://999.md/ru/{lid}", price_original=pps * area, area_sot=area,
                           lat=lat, lon=lon, settlement_id=settlement_id, settlement=settlement, **extra)
    repo.upsert_listing(db, listing, status=status, filter_reason=None, now=T0)
    db.commit()


def services(routes=None, pois=()):
    return Services(
        routes=routes if routes is not None else AnyRoute(NEAR),
        poi_index=PoiIndex(list(pois)),
        rates=None,
        settlements={SINGERA: SettlementRef(id=SINGERA, name="Сынжера", potential="Высокий", score=85)},
    )


def scores(db):
    return {row["listing_id"]: row for row in db.execute("SELECT * FROM scores")}


def six_normal(db):
    for i, pps in enumerate([4000, 4500, 5000, 5500, 6000, 6500]):
        add(db, f"n{i}", pps, lat=46.930 + i * 0.002)


def test_ranking_by_price_when_everything_else_is_equal(db):
    six_normal(db)
    summary = rescore(db, SETTINGS, services(), now=T0)
    s = scores(db)
    assert (summary.scored, summary.eligible) == (6, 6)
    assert [s[f"n{i}"]["rank"] for i in range(6)] == [1, 2, 3, 4, 5, 6]
    first = s["n0"]
    assert (first["median_level"], first["median_n"], first["market_median_eur_per_sot"]) == ("settlement", 6, 5250)
    components = json.loads(first["components"])
    assert components["price"] == pytest.approx(50 + 50 * (1250 / 5250) / 0.4)
    assert components["route"] == pytest.approx(80.0)
    assert components["settlement"] == 85
    assert components["utilities"] == 90
    assert (first["nearest_entry"], first["route_km"]) == ("M3", 8.0)
    assert "село Сынжера" in json.loads(first["explanations"])["price"]
    assert summary.best_total == first["total"]


def test_exclusion_reasons(db):
    six_normal(db)
    add(db, "over", 9000)
    add(db, "far", 5000, lat=47.300)
    add(db, "noroute", 5000, lat=47.400)
    add(db, "nocoords", 5000, lat=None, lon=None, settlement_id=77777, settlement="Нигде")
    add(db, "cheap", 500, lat=46.960)
    add(db, "dup", 4100, lat=46.930)
    add(db, "city", 6000, lat=47.020, lon=28.830, settlement_id=CITY, settlement="Кишинёв")
    routes = AnyRoute(NEAR)
    routes[round_key(47.300, 28.940)] = Route(km=40.0, minutes=45.0, entry="M3")
    routes[round_key(47.400, 28.940)] = None
    rescore(db, SETTINGS, services(routes), now=T0)
    s = scores(db)
    reasons = {lid: row["excluded_reason"] for lid, row in s.items()}
    assert reasons["over"] == "over_budget"
    assert reasons["far"] == "too_far"
    assert reasons["noroute"] == "no_route"
    assert reasons["nocoords"] == "no_coords"
    assert reasons["cheap"] == "suspicious_price"
    assert reasons["dup"] == "duplicate"
    assert reasons["n0"] is None and reasons["city"] is None
    assert (s["city"]["route_km"], s["city"]["nearest_entry"]) == (0, IN_CITY_ENTRY)
    assert "suspicious_price" in json.loads(s["cheap"]["flags"])
    assert (s["dup"]["duplicate_group"], s["dup"]["is_group_primary"]) == ("dup", 0)
    assert (s["n0"]["duplicate_group"], s["n0"]["is_group_primary"]) == ("dup", 1)
    assert s["over"]["rank"] is None


def test_listing_without_coordinates_uses_settlement_centre(db):
    six_normal(db)
    add(db, "approx", 5000, lat=None, lon=None)
    rescore(db, SETTINGS, services(), now=T0)
    row = scores(db)["approx"]
    assert (row["coords_approx"], row["excluded_reason"]) == (1, None)
    assert row["lat"] == pytest.approx(46.935) and row["lon"] == pytest.approx(28.940)
    assert "coords_approx" in json.loads(row["flags"])


def test_recently_removed_listing_feeds_the_median(db):
    for i, pps in enumerate([4000, 5000, 6000, 7000]):
        add(db, f"a{i}", pps, lat=46.930 + i * 0.002)
    add(db, "gone", 8000, lat=46.950, status="removed")
    rescore(db, SETTINGS, services(), now=T0 + timedelta(days=1))
    s = scores(db)
    assert set(s) == {"a0", "a1", "a2", "a3"}
    assert (s["a0"]["median_level"], s["a0"]["median_n"], s["a0"]["market_median_eur_per_sot"]) == (
        "settlement", 5, 6000)


def test_old_removed_listing_is_ignored(db):
    for i, pps in enumerate([4000, 5000, 6000, 7000]):
        add(db, f"a{i}", pps, lat=46.930 + i * 0.002)
    add(db, "gone", 8000, lat=46.950, status="removed")
    rescore(db, SETTINGS, services(), now=T0 + timedelta(days=100))
    row = scores(db)["a0"]
    assert (row["median_level"], row["median_n"]) == ("region", 4)


def test_infrastructure_uses_poi_index(db):
    add(db, "solo", 5000)
    rescore(db, SETTINGS, services(pois=[Poi("node/1", "supermarket", "Linella", None, 46.9336, 28.940)]), now=T0)
    row = scores(db)["solo"]
    assert json.loads(row["poi_nearest_m"])["supermarket"] == pytest.approx(400, abs=2)
    assert json.loads(row["components"])["infrastructure"] == pytest.approx(30.0)


def test_rescore_replaces_previous_snapshot(db):
    six_normal(db)
    rescore(db, SETTINGS, services(), now=T0)
    rescore(db, SETTINGS, services(), now=T0)
    assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 6


def test_route_points(db):
    add(db, "p1", 5000, lat=46.93012, lon=28.94049)
    add(db, "p2", 5000, lat=None, lon=None)
    add(db, "city", 6000, lat=47.02, lon=28.83, settlement_id=CITY, settlement="Кишинёв")
    repo.upsert_listing(db, make_listing(id="f", lat=46.5, lon=28.5, settlement_id=SINGERA),
                        status="filtered_out", filter_reason="region", now=T0)
    db.commit()
    assert route_points(db, SETTINGS, now=T0) == {round_key(46.93012, 28.94049)}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/scoring/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.scoring.engine'`.

- [ ] **Step 3: Реализовать `src/homealize/storage/scores.py`**

```python
"""Снимок оценки: перезаписывается целиком при каждом пересчёте. Функции не делают commit."""

import sqlite3

SCORE_COLUMNS = (
    "listing_id",
    "computed_at",
    "price_eur",
    "price_per_sot_eur",
    "lat",
    "lon",
    "coords_approx",
    "route_km",
    "route_min",
    "nearest_entry",
    "poi_nearest_m",
    "market_median_eur_per_sot",
    "median_level",
    "median_n",
    "duplicate_group",
    "is_group_primary",
    "components",
    "explanations",
    "flags",
    "total",
    "excluded_reason",
    "rank",
)


def replace_scores(conn: sqlite3.Connection, records: list[dict[str, object]]) -> None:
    conn.execute("DELETE FROM scores")
    if records:
        conn.executemany(
            f"INSERT INTO scores ({', '.join(SCORE_COLUMNS)}) VALUES ({', '.join('?' * len(SCORE_COLUMNS))})",
            [[record[column] for column in SCORE_COLUMNS] for record in records],
        )


def load_scored(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Оценённые объявления вместе с полями объявления: сначала рейтинг по месту, потом остальные."""
    return conn.execute(
        "SELECT s.*, l.title, l.url, l.region, l.settlement, l.zone, l.area_sot, l.price_original, l.currency, "
        "l.author_type, l.first_seen_at "
        "FROM scores s JOIN listings l ON l.id = s.listing_id "
        "ORDER BY s.rank IS NULL, s.rank, s.total DESC, s.listing_id"
    ).fetchall()
```

- [ ] **Step 4: Реализовать `src/homealize/scoring/engine.py`**

```python
"""Оценка участков: местоположение, рыночные медианы, компоненты балла, исключения и рейтинг (спека §6, §12).

Движок не ходит в сеть: маршруты, POI, курс и справочник сёл передаются через Services.
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median

from homealize.config import ScoringSettings, Settings
from homealize.enrich.currency import to_eur
from homealize.enrich.poi import PoiIndex
from homealize.enrich.routing import round_key
from homealize.models import Route
from homealize.scoring.components import (
    IN_CITY_ENTRY,
    Component,
    infra_component,
    price_component,
    route_component,
    settlement_component,
    utilities_component,
    weighted_total,
)
from homealize.scoring.market import DupItem, MarketItem, MarketMedians, Median, group_duplicates, is_suspicious
from homealize.settlements import SettlementRef
from homealize.storage.scores import replace_scores


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    approx: bool  # True — центр села, а не точка из объявления


@dataclass(frozen=True)
class Services:
    routes: dict[tuple[float, float], Route | None]
    poi_index: PoiIndex
    rates: dict[str, float] | None
    settlements: dict[int, SettlementRef]


@dataclass
class ScoreSummary:
    scored: int = 0  # активных объявлений с посчитанным баллом
    eligible: int = 0  # из них в рейтинге
    best_total: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Candidate:
    row: sqlite3.Row
    location: Location | None
    route: Route | None
    in_city: bool
    price_eur: float | None
    price_per_sot: float | None


def load_rows(conn: sqlite3.Connection, settings: Settings, *, now: datetime) -> list[sqlite3.Row]:
    """Активные объявления и снятые за median_removed_days (для медиан), прошедшие фильтры этапа 1."""
    cutoff = (now.astimezone(UTC) - timedelta(days=settings.scoring.median_removed_days)).isoformat()
    return conn.execute(
        "SELECT * FROM listings WHERE filter_reason IS NULL "
        "AND (status = 'active' OR (status = 'removed' AND last_seen_at >= ?)) ORDER BY id",
        (cutoff,),
    ).fetchall()


def locate(rows: list[sqlite3.Row]) -> dict[str, Location | None]:
    """Точка из объявления, иначе медиана координат объявлений того же села, иначе None."""
    points: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        if row["lat"] is not None and row["lon"] is not None and row["settlement_id"] is not None:
            points[row["settlement_id"]].append((row["lat"], row["lon"]))
    centres = {
        settlement: (median(p[0] for p in pts), median(p[1] for p in pts)) for settlement, pts in points.items()
    }
    locations: dict[str, Location | None] = {}
    for row in rows:
        if row["lat"] is not None and row["lon"] is not None:
            locations[row["id"]] = Location(row["lat"], row["lon"], approx=False)
        elif row["settlement_id"] in centres:
            lat, lon = centres[row["settlement_id"]]
            locations[row["id"]] = Location(lat, lon, approx=True)
        else:
            locations[row["id"]] = None
    return locations


def route_points(conn: sqlite3.Connection, settings: Settings, *, now: datetime) -> set[tuple[float, float]]:
    """Точки (round_key), для которых оценке нужен маршрут OSRM."""
    rows = load_rows(conn, settings, now=now)
    locations = locate(rows)
    city = set(settings.routing.city_settlement_ids)
    return {
        round_key(locations[row["id"]].lat, locations[row["id"]].lon)
        for row in rows
        if locations[row["id"]] is not None and row["settlement_id"] not in city
    }


def rescore(conn: sqlite3.Connection, settings: Settings, services: Services, *, now: datetime) -> ScoreSummary:
    scoring = settings.scoring
    rows = load_rows(conn, settings, now=now)
    locations = locate(rows)
    city = set(settings.routing.city_settlement_ids)
    candidates = [_candidate(row, locations[row["id"]], services, city) for row in rows]
    active = [c for c in candidates if c.row["status"] == "active"]

    duplicates = group_duplicates(
        [
            DupItem(c.row["id"], c.row["area_sot"], c.location.lat, c.location.lon, c.price_eur)
            for c in active
            if c.location is not None and not c.location.approx and c.price_eur is not None
        ],
        distance_m=scoring.duplicate_distance_m,
        price_tolerance=scoring.duplicate_price_tolerance,
    )

    names = {c.row["settlement_id"]: c.row["settlement"] for c in candidates if c.row["settlement_id"] is not None}
    max_km = settings.routing.max_route_km
    market = [
        c for c in candidates
        if c.route is not None and c.route.km <= max_km and c.price_per_sot is not None
        and duplicates.get(c.row["id"], (None, True))[1]
    ]
    first_pass = _medians(market, scoring, names)
    clean = [
        c for c in market
        if not is_suspicious(c.price_per_sot, first_pass.for_listing(c.row["settlement_id"], c.route.km),
                             scoring.suspicious_ratio)
    ]
    medians = _medians(clean, scoring, names)

    computed_at = now.astimezone(UTC).isoformat()
    records = []
    for c in active:
        market_median = medians.for_listing(c.row["settlement_id"], c.route.km if c.route else None)
        suspicious = is_suspicious(c.price_per_sot, market_median, scoring.suspicious_ratio)
        group, primary = duplicates.get(c.row["id"], (None, True))
        nearest = services.poi_index.nearest_m(c.location.lat, c.location.lon) if c.location else {}
        components, flags = _components(c, market_median, suspicious, nearest, services, scoring)
        records.append({
            "listing_id": c.row["id"],
            "computed_at": computed_at,
            "price_eur": c.price_eur,
            "price_per_sot_eur": c.price_per_sot,
            "lat": c.location.lat if c.location else None,
            "lon": c.location.lon if c.location else None,
            "coords_approx": bool(c.location and c.location.approx),
            "route_km": c.route.km if c.route else None,
            "route_min": c.route.minutes if c.route else None,
            "nearest_entry": c.route.entry if c.route else None,
            "poi_nearest_m": json.dumps(nearest, ensure_ascii=False),
            "market_median_eur_per_sot": market_median.value if market_median else None,
            "median_level": market_median.level if market_median else None,
            "median_n": market_median.n if market_median else None,
            "duplicate_group": group,
            "is_group_primary": primary,
            "components": json.dumps({name: comp.score for name, comp in components.items()}),
            "explanations": json.dumps({name: comp.note for name, comp in components.items()}, ensure_ascii=False),
            "flags": json.dumps(flags, ensure_ascii=False),
            "total": weighted_total(components, scoring.weights),
            "excluded_reason": _excluded_reason(c, suspicious, primary, settings),
            "rank": None,
        })

    eligible = sorted(
        (r for r in records if r["excluded_reason"] is None),
        key=lambda r: (-r["total"], r["price_eur"], r["listing_id"]),
    )
    for position, record in enumerate(eligible, start=1):
        record["rank"] = position
    replace_scores(conn, records)
    return ScoreSummary(
        scored=len(records),
        eligible=len(eligible),
        best_total=eligible[0]["total"] if eligible else None,
    )


def _candidate(row: sqlite3.Row, location: Location | None, services: Services, city: set[int]) -> _Candidate:
    in_city = row["settlement_id"] in city
    if in_city:
        route = Route(km=0.0, minutes=0.0, entry=IN_CITY_ENTRY)
    elif location is None:
        route = None
    else:
        route = services.routes.get(round_key(location.lat, location.lon))
    price_eur = to_eur(row["price_original"], row["currency"], services.rates)
    area = row["area_sot"]
    price_per_sot = round(price_eur / area, 2) if price_eur is not None and area else None
    return _Candidate(row, location, route, in_city, price_eur, price_per_sot)


def _medians(candidates: list[_Candidate], scoring: ScoringSettings, names: dict[int, str]) -> MarketMedians:
    return MarketMedians(
        [MarketItem(c.row["id"], c.row["settlement_id"], c.route.km, c.price_per_sot) for c in candidates],
        min_settlement=scoring.median_min_settlement,
        min_route_band=scoring.median_min_route_band,
        band_km=scoring.median_route_band_km,
        settlement_names=names,
    )


def _components(
    c: _Candidate,
    market_median: Median | None,
    suspicious: bool,
    nearest: dict[str, int | None],
    services: Services,
    scoring: ScoringSettings,
) -> tuple[dict[str, Component], list[str]]:
    price = price_component(
        c.price_per_sot,
        market_median.value if market_median else None,
        label=market_median.label if market_median else "",
        n=market_median.n if market_median else 0,
        full_discount=scoring.price_full_discount,
        suspicious=suspicious,
    )
    route = route_component(c.route, best_min=scoring.route_best_min, worst_min=scoring.route_worst_min)
    infra = infra_component(nearest, weights=scoring.poi_weights, full_m=scoring.poi_full_m, zero_m=scoring.poi_zero_m)
    utilities, utility_flags = utilities_component(
        {utility: c.row[f"has_{utility}"] for utility in scoring.utility_weights}, weights=scoring.utility_weights
    )
    settlement, settlement_flags = settlement_component(
        services.settlements.get(c.row["settlement_id"]), unknown_score=scoring.unknown_settlement_score
    )
    flags = [*utility_flags, *settlement_flags]
    if suspicious:
        flags.append("suspicious_price")
    if c.location is None:
        flags.append("no_coords")
    elif c.location.approx:
        flags.append("coords_approx")
    components = {
        "price": price,
        "route": route,
        "infrastructure": infra,
        "utilities": utilities,
        "settlement": settlement,
    }
    return components, flags


def _excluded_reason(c: _Candidate, suspicious: bool, primary: bool, settings: Settings) -> str | None:
    if c.location is None and not c.in_city:
        return "no_coords"
    if c.route is None:
        return "no_route"
    if c.route.km > settings.routing.max_route_km:
        return "too_far"
    if c.price_eur is None:
        return "no_price_eur"
    if c.price_eur > settings.scoring.max_price_eur:
        return "over_budget"
    if suspicious:
        return "suspicious_price"
    if not primary:
        return "duplicate"
    return None
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `uv run pytest tests/scoring -v`
Expected: 7 (market) + 14 (components) + 8 (engine) passed.

Run: `uv run pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/storage/scores.py src/homealize/scoring/engine.py tests/scoring/test_engine.py
git commit -m "feat: scoring engine with exclusions, ranking and score snapshot"
```

---

### Task 9: Выгрузка в Excel

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (через `uv add`)
- Create: `src/homealize/export/__init__.py`, `src/homealize/export/excel.py`, `tests/export/__init__.py`
- Test: `tests/export/test_excel.py`

**Interfaces:**
- Consumes: `ExportSettings` (Task 1); `load_scored`, `replace_scores`, `SCORE_COLUMNS` (Task 8); `FLAG_LABELS`, `EXCLUSION_LABELS` (Task 7); таблица `price_history` (этап 1).
- Produces (`homealize.export.excel`):
  - `ExportError(Exception)`;
  - `export_workbook(conn, settings: ExportSettings, *, now) -> Path`:
    - пишет `latest.xlsx` и `homealize_ГГГГ-ММ-ДД.xlsx` (дата UTC);
    - удаляет дневные копии старше `keep_days`;
    - листы: «Топ», «Все активные», «Изменения цен», «Медианы по сёлам».

- [ ] **Step 1: Добавить зависимость**

Run: `uv add "openpyxl>=3.1"`
Expected: `pyproject.toml` и `uv.lock` обновлены, openpyxl установлен.

- [ ] **Step 2: Написать падающие тесты**

`tests/export/__init__.py`: пустой файл.

`tests/export/test_excel.py`:

```python
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
    rows = list(load_workbook(path)["Изменения цен"].iter_rows(min_row=2, values_only=True))
    assert rows == [
        ("2026-10-29", "Гратиешты", 6.0, 30000.0, 27000.0, "EUR", -10.0, "открыть"),
        ("2026-10-25", "Гратиешты", 6.0, 33000.0, 30000.0, "EUR", -9.1, "открыть"),
    ]


def test_medians_sheet(db, tmp_path):
    add_listing(db, "a")
    add_listing(db, "b")
    add_listing(db, "c")
    replace_scores(db, [
        record("a", price_per_sot_eur=5000.0),
        record("b", price_per_sot_eur=7000.0, excluded_reason="over_budget", rank=None),
        record("c", price_per_sot_eur=100.0, excluded_reason="suspicious_price", rank=None),
    ])
    db.commit()
    path = export_workbook(db, ExportSettings(dir=tmp_path), now=T0)
    rows = list(load_workbook(path)["Медианы по сёлам"].iter_rows(min_row=2, values_only=True))
    assert rows == [("Гратиешты", "Кишинёв мун.", 2, 6000)]


def test_daily_copy_and_pruning(db, tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "homealize_2026-09-01.xlsx").write_bytes(b"old")
    (exports / "homealize_2026-10-20.xlsx").write_bytes(b"recent")
    (exports / "notes.txt").write_text("keep", encoding="utf-8")
    export_workbook(db, ExportSettings(dir=exports, keep_days=30), now=T0)
    assert sorted(p.name for p in exports.iterdir()) == [
        "homealize_2026-10-20.xlsx",
        "homealize_2026-10-30.xlsx",
        "latest.xlsx",
        "notes.txt",
    ]


def test_locked_file_gives_export_error(db, tmp_path, monkeypatch):
    def locked(self, filename):
        raise PermissionError(13, "Permission denied", str(filename))

    monkeypatch.setattr("homealize.export.excel.Workbook.save", locked)
    with pytest.raises(ExportError, match="открыт"):
        export_workbook(db, ExportSettings(dir=tmp_path), now=T0)
```

Пояснение к ожидаемым значениям в `test_price_changes_sheet`:
- изменение цены 40 дней назад (с 33 000 до 30 000) старше 30 дней, поэтому само в лист не попадает;
- следующее изменение сравнивается с предыдущей ценой, даже если та старше 30 дней: 33 000 → 30 000 пять дней назад, −9.1%;
- последнее изменение: 30 000 → 27 000 вчера, −10%;
- новые строки идут первыми.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/export -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.export'`.

- [ ] **Step 4: Реализовать**

`src/homealize/export/__init__.py`:

```python
"""Выгрузка результатов."""
```

`src/homealize/export/excel.py`:

```python
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
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `uv run pytest tests/export -v`
Expected: 5 passed.

Run: `uv run pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/homealize/export tests/export
git commit -m "feat: Excel export of the ranking, price changes and medians"
```

---

### Task 10: Оценка после обхода, команды CLI, документация, живой прогон

**Files:**
- Create: `src/homealize/evaluate.py`, `tests/test_evaluate.py`, `tests/live/test_live_services.py`
- Modify: `src/homealize/cli.py` (заменить целиком), `tests/test_cli.py`, `README.md` (заменить целиком), `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` (добавить §12)

**Interfaces:**
- Consumes:
  - `ensure_pois`, `PoiIndex`, `fetch_pois` (Task 3);
  - `OsrmRouter`, `ensure_routes` (Task 4);
  - `ensure_rates`, `fetch_bnm_rates` (Task 5);
  - `rescore`, `route_points`, `Services`, `ScoreSummary` (Task 8);
  - `export_workbook`, `ExportError` (Task 9);
  - `load_settlements` (Task 1); `run_once` (этап 1).
- Produces:
  - `homealize.evaluate`:
    - `USER_AGENT`;
    - `build_http_client() -> httpx.Client`;
    - `evaluate(conn, settings, http, *, now, force_poi=False, sleep=time.sleep) -> ScoreSummary` — сама делает commit.
  - CLI-команды `run` (обход, оценка, Excel), `rescore`, `export`, `refresh-poi`; коды выхода 0/1/2/3.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_evaluate.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import httpx

from homealize.config import Settings
from homealize.evaluate import evaluate
from homealize.storage import repo
from tests.factories import make_listing

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = Settings.model_validate(
    {"scoring": {"settlements_path": str(PROJECT_ROOT / "config" / "settlements.yaml")}}
)
OVERPASS = {"elements": [{"type": "node", "id": 1, "lat": 46.931, "lon": 28.94, "tags": {"shop": "supermarket"}}]}
BNM_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><ValCurs Date="30.10.2026">'
    "<Valute ID=\"47\"><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>20.1303</Value></Valute></ValCurs>"
)


def services_handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "overpass-api.de":
            return httpx.Response(200, json=OVERPASS)
        if request.url.host == "www.bnm.md":
            return httpx.Response(200, text=BNM_XML)
        if request.url.host == "router.project-osrm.org":
            n = len(request.url.params["sources"].split(";"))
            k = len(request.url.params["destinations"].split(";"))
            return httpx.Response(200, json={"code": "Ok", "durations": [[600.0] * k] * n,
                                              "distances": [[8000.0] * k] * n})
        return httpx.Response(404)

    return handler


def add_three(db):
    for i in range(3):
        repo.upsert_listing(
            db,
            make_listing(id=f"x{i}", lat=46.93 + i * 0.01, lon=28.94, settlement_id=14398, settlement="Сынжера"),
            status="active", filter_reason=None, now=T0,
        )
    db.commit()


def test_evaluate_scores_listings_and_reuses_caches(db):
    add_three(db)
    calls = []
    http = httpx.Client(transport=httpx.MockTransport(services_handler(calls)))
    summary = evaluate(db, SETTINGS, http, now=T0, sleep=lambda seconds: None)
    assert summary.warnings == []
    assert (summary.scored, summary.eligible) == (3, 3)
    assert sorted(set(calls)) == ["overpass-api.de", "router.project-osrm.org", "www.bnm.md"]
    calls.clear()
    evaluate(db, SETTINGS, http, now=T0, sleep=lambda seconds: None)
    assert calls == []


def test_service_failures_become_warnings(db):
    add_three(db)
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    summary = evaluate(db, SETTINGS, http, now=T0, sleep=lambda seconds: None)
    assert len(summary.warnings) == 3
    assert any("POI не обновлены" in w for w in summary.warnings)
    assert any("Курс BNM не получен" in w for w in summary.warnings)
    assert any("Маршруты не посчитаны" in w for w in summary.warnings)
    assert (summary.scored, summary.eligible) == (3, 0)
```

В `tests/test_cli.py`:
- функцию `write_config` заменить на версию ниже: она добавляет папку Excel во временный каталог;
- добавить автоматическую фикстуру, которая подменяет оценку, чтобы тесты CLI не ходили в сеть;
- добавить новые тесты:

```python
from homealize.models import Poi
from homealize.scoring.engine import ScoreSummary


def write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"storage:\n  db_path: '{(tmp_path / 'data' / 'test.db').as_posix()}'\n"
        f"logging:\n  path: '{(tmp_path / 'logs' / 'test.log').as_posix()}'\n"
        f"export:\n  dir: '{(tmp_path / 'exports').as_posix()}'\n",
        encoding="utf-8",
    )
    return cfg


@pytest.fixture(autouse=True)
def _fake_evaluation(monkeypatch):
    calls = []

    def fake_evaluate(conn, settings, http, *, now, force_poi=False, sleep=None):
        calls.append(force_poi)
        return ScoreSummary(scored=2, eligible=1, best_total=71.5, warnings=["тестовое предупреждение"])

    monkeypatch.setattr(cli, "evaluate", fake_evaluate)
    return calls


def test_run_evaluates_and_writes_excel(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))
    assert cli.main(["--config", str(cfg), "run"]) == 0
    out = capsys.readouterr().out
    assert "Оценка: оценено 2, в рейтинге 1, лучший балл 72" in out
    assert "Предупреждение: тестовое предупреждение" in out
    assert (tmp_path / "exports" / "latest.xlsx").exists()


def test_run_returns_3_when_evaluation_fails(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))

    def broken(*args, **kwargs):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(cli, "evaluate", broken)
    assert cli.main(["--config", str(cfg), "run"]) == 3
    assert "Оценка не выполнена: RuntimeError: сломалось" in capsys.readouterr().err


def test_rescore_does_not_scan(tmp_path, capsys, monkeypatch, _fake_evaluation):
    cfg = write_config(tmp_path)

    def no_source(settings):
        raise AssertionError("rescore не должен обходить сайт")

    monkeypatch.setattr(cli, "build_nine99_source", no_source)
    assert cli.main(["--config", str(cfg), "rescore"]) == 0
    assert _fake_evaluation == [False]
    assert "Оценка:" in capsys.readouterr().out


def test_export_writes_excel(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "export"]) == 0
    assert (tmp_path / "exports" / "latest.xlsx").exists()
    assert "Excel:" in capsys.readouterr().out


def test_refresh_poi(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    pois = [Poi("node/1", "school", None, None, 47.0, 28.8), Poi("node/2", "pharmacy", None, None, 47.0, 28.8)]
    monkeypatch.setattr(cli, "ensure_pois", lambda conn, http, settings, *, now, force: (pois, None))
    assert cli.main(["--config", str(cfg), "refresh-poi"]) == 0
    assert "Инфраструктура обновлена: 2 объектов" in capsys.readouterr().out


def test_refresh_poi_failure_returns_3(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "ensure_pois",
                        lambda conn, http, settings, *, now, force: ([], "POI не обновлены (HTTPError: 503)"))
    assert cli.main(["--config", str(cfg), "refresh-poi"]) == 3
    assert "POI не обновлены" in capsys.readouterr().err
```

`tests/live/test_live_services.py`:

```python
"""Проверка внешних сервисов оценки (по одному небольшому запросу). Запускать вручную: uv run pytest -m live."""

from datetime import date

import pytest

from homealize.config import CurrencySettings, PoiSettings, RoutingSettings
from homealize.enrich.currency import fetch_bnm_rates
from homealize.enrich.poi import fetch_pois
from homealize.enrich.routing import OsrmRouter
from homealize.evaluate import build_http_client

pytestmark = pytest.mark.live


def test_osrm_routes_from_singera():
    with build_http_client() as http:
        route = OsrmRouter(http, RoutingSettings()).table([(46.931, 28.939)])[(46.931, 28.939)]
    assert route is not None
    assert 0 < route.minutes < 30


def test_bnm_rates():
    with build_http_client() as http:
        rates = fetch_bnm_rates(http, CurrencySettings(), date(2026, 10, 29))
    assert 15 < rates["EUR"] < 30


def test_overpass_small_area():
    with build_http_client() as http:
        pois = fetch_pois(http, PoiSettings(radius_km=1.0))
    assert len(pois) >= 10
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_evaluate.py tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.evaluate'`.

- [ ] **Step 3: Реализовать `src/homealize/evaluate.py`**

```python
"""Оценка этапа 2: подготовка внешних данных (POI, курс, маршруты) и пересчёт баллов."""

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from homealize.config import Settings
from homealize.enrich.currency import ensure_rates
from homealize.enrich.poi import PoiIndex, ensure_pois
from homealize.enrich.routing import OsrmRouter, ensure_routes
from homealize.scoring.engine import ScoreSummary, Services, rescore, route_points
from homealize.settlements import load_settlements

log = logging.getLogger(__name__)

USER_AGENT = "homealize/0.1 (personal land monitor)"


def build_http_client() -> httpx.Client:
    """Клиент для внешних сервисов оценки (не для 999.md)."""
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def evaluate(
    conn: sqlite3.Connection,
    settings: Settings,
    http: httpx.Client,
    *,
    now: datetime,
    force_poi: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> ScoreSummary:
    """Обновляет кэши внешних данных и пересчитывает снимок оценки. Ошибки сервисов становятся предупреждениями."""
    warnings: list[str] = []
    settlements = load_settlements(settings.scoring.settlements_path)
    pois, warning = ensure_pois(conn, http, settings.poi, now=now, force=force_poi)
    _note(warnings, warning)
    rates, warning = ensure_rates(conn, http, settings.currency, day=now.date())
    _note(warnings, warning)
    router = OsrmRouter(http, settings.routing)
    routes, warning = ensure_routes(conn, router, route_points(conn, settings, now=now), now=now, sleep=sleep)
    _note(warnings, warning)
    summary = rescore(conn, settings, Services(routes, PoiIndex(pois), rates, settlements), now=now)
    conn.commit()
    summary.warnings = warnings + summary.warnings
    log.info("Оценка: оценено %s, в рейтинге %s, лучший балл %s", summary.scored, summary.eligible,
             summary.best_total)
    return summary


def _note(warnings: list[str], warning: str | None) -> None:
    if warning:
        log.warning(warning)
        warnings.append(warning)
```

- [ ] **Step 4: Заменить `src/homealize/cli.py` целиком**

```python
"""Командная строка: homealize init-db | run | rescore | export | refresh-poi."""

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from homealize.config import Settings, load_settings
from homealize.enrich.poi import ensure_pois
from homealize.evaluate import build_http_client, evaluate
from homealize.export.excel import ExportError, export_workbook
from homealize.logging_setup import configure_logging
from homealize.models import RunSummary
from homealize.pipeline import run_once
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage.db import connect, migrate

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("config/settings.yaml")
EVALUATION_FAILED = 3


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="homealize", description="Мониторинг участков под строительство на 999.md")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="путь к settings.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="создать или обновить базу")
    commands.add_parser("run", help="обход сайта, оценка участков и выгрузка Excel")
    commands.add_parser("rescore", help="пересчитать баллы и выгрузить Excel без обхода сайта")
    commands.add_parser("export", help="выгрузить Excel из уже посчитанных баллов")
    commands.add_parser("refresh-poi", help="заново скачать инфраструктуру из OpenStreetMap")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:  # ValueError: не UTF-8 и ошибки pydantic
        print(f"Не удалось прочитать настройки {args.config}: {exc}", file=sys.stderr)
        return 2
    handlers = {
        "init-db": _init_db,
        "run": _run,
        "rescore": _rescore,
        "export": _export,
        "refresh-poi": _refresh_poi,
    }
    return handlers[args.command](settings)


def _init_db(settings: Settings) -> int:
    conn = connect(settings.storage.db_path)
    try:
        version = migrate(conn)
    finally:
        conn.close()
    print(f"База готова: {settings.storage.db_path} (схема v{version})")
    return 0


def _run(settings: Settings) -> int:
    configure_logging(settings.logging)
    conn = connect(settings.storage.db_path)
    source = build_nine99_source(settings)
    try:
        migrate(conn)
        now = datetime.now(UTC)
        summary = run_once(conn, source, settings, now=now)
        _print_scan(summary)
        evaluated = _evaluate_and_export(conn, settings, now=now)
    finally:
        source.close()
        conn.close()
    if not summary.search_complete:
        return 1
    return 0 if evaluated else EVALUATION_FAILED


def _rescore(settings: Settings) -> int:
    configure_logging(settings.logging)
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        evaluated = _evaluate_and_export(conn, settings, now=datetime.now(UTC))
    finally:
        conn.close()
    return 0 if evaluated else EVALUATION_FAILED


def _export(settings: Settings) -> int:
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        path = export_workbook(conn, settings.export, now=datetime.now(UTC))
    except ExportError as exc:
        print(f"Excel не сохранён: {exc}", file=sys.stderr)
        return EVALUATION_FAILED
    finally:
        conn.close()
    print(f"Excel: {path}")
    return 0


def _refresh_poi(settings: Settings) -> int:
    configure_logging(settings.logging)
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        with build_http_client() as http:
            pois, warning = ensure_pois(conn, http, settings.poi, now=datetime.now(UTC), force=True)
    finally:
        conn.close()
    if warning:
        print(warning, file=sys.stderr)
        return EVALUATION_FAILED
    print(f"Инфраструктура обновлена: {len(pois)} объектов")
    return 0


def _evaluate_and_export(conn, settings: Settings, *, now: datetime) -> bool:
    try:
        with build_http_client() as http:
            result = evaluate(conn, settings, http, now=now)
        path = export_workbook(conn, settings.export, now=now)
    except ExportError as exc:
        print(f"Excel не сохранён: {exc}", file=sys.stderr)
        return False
    except Exception as exc:  # оценка не должна терять уже сохранённый обход: сообщаем и выходим с кодом 3
        log.exception("Оценка не выполнена")
        print(f"Оценка не выполнена: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    best = f"{result.best_total:.0f}" if result.best_total is not None else "—"
    print(f"Оценка: оценено {result.scored}, в рейтинге {result.eligible}, лучший балл {best}. Excel: {path}")
    for warning in result.warnings:
        print(f"Предупреждение: {warning}")
    return True


def _print_scan(summary: RunSummary) -> None:
    state = "успешен" if summary.search_complete else "НЕ завершён"
    print(
        f"Прогон {state}: увидено {summary.ads_seen}, новых {summary.new}, "
        f"цена изменилась {summary.price_changed}, обновлено {summary.changed}, "
        f"снято {summary.removed}, ошибок {len(summary.errors)}"
    )


def _force_utf8_output() -> None:
    """В Windows вывод в пайп идёт в кодировке системы; кириллица без этого может упасть."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Убедиться, что офлайн-тесты проходят**

Run: `uv run pytest -q -W error`
Expected: весь набор зелёный, live-тесты не запускаются.

- [ ] **Step 6: Обновить документацию**

`README.md` — заменить целиком:

````markdown
# Homealize

Мониторинг участков под строительство дома на 999.md: сбор объявлений, история цен, оценка участков по «весам» и рейтинг в Excel.

Спецификация: `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md`.

## Быстрый старт

```bash
uv sync
uv run homealize init-db
uv run homealize run
```

`run` делает два шага:

1. Обходит поиск 999.md (около 23 запросов с паузами 2–3 с, примерно минута) и сохраняет объявления.
2. Оценивает активные участки и пишет рейтинг в `exports/latest.xlsx`, плюс копию за день `exports/homealize_ГГГГ-ММ-ДД.xlsx`. Копии хранятся 30 дней.

Для оценки программа обращается к внешним сервисам:

- **OpenStreetMap (Overpass)**: магазины, школы, детсады, аптеки, медпункты, остановки. Скачиваются раз в 30 дней, запрос занимает около минуты.
- **OSRM**: время и расстояние по дорогам до ближайшего из 14 въездов в Кишинёв. Считается один раз для каждой точки и запоминается.
- **Нацбанк Молдовы**: курс для цен в леях и долларах, раз в день.

Если сервис недоступен, используются сохранённые данные, а в выводе появляется строка «Предупреждение: …».

- Настройки: `config/settings.yaml` (веса, бюджет, пороги, точки въезда).
- Справочник сёл: `config/settlements.yaml`.
- База: `data/homealize.db`. Логи: `logs/homealize.log`.

## Команды

| Команда | Что делает |
|---|---|
| `init-db` | создаёт или обновляет базу |
| `run` | обход сайта, оценка, Excel |
| `rescore` | оценка и Excel без обхода сайта, например после правки весов |
| `export` | только Excel из уже посчитанных баллов |
| `refresh-poi` | заново скачать инфраструктуру из OpenStreetMap |

Команды нужно запускать из корня проекта: пути к настройкам, базе, логам и Excel относительные. Другой файл настроек задаётся параметром `--config PATH` перед командой:

```bash
uv run homealize --config путь/к/settings.yaml run
```

Коды выхода:

- `0` — всё выполнено;
- `1` — обход прерван или ему нельзя доверять (ошибка сети, лимит запросов, неожиданный ответ сайта, слишком много нераспознанных объявлений). Сохранённые страницы остаются, пропавшие объявления не снимаются;
- `2` — настройки не удалось прочитать;
- `3` — обход прошёл, но оценка или сохранение Excel не удались.

Если `latest.xlsx` открыт в Excel, сохранить его нельзя: закройте файл и выполните `uv run homealize export`.

## Тесты

```bash
uv run pytest
uv run pytest -m live
```

Первая команда запускает офлайн-тесты. Вторая делает по одному настоящему запросу к 999.md, OSRM, Нацбанку и небольшой запрос к Overpass и проверяет, что форматы ответов не изменились.
````

В конец спеки `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` добавить:

```markdown

## 12. Уточнения при планировании этапа 2 (2026-10-30)

Этот раздел уточняет разделы 4–7 по итогам этапа 1 и проверки внешних сервисов; где он расходится с ними, действует он.

- **Статус объявления.** `listings.status` и `filter_reason` отражают только фильтры этапа 1 (тип, площадь, регион, наличие цены) и жизненный цикл (на сайте / снято). Исключения этапа 2 хранятся в `scores.excluded_reason` и пересчитываются при каждой оценке, поэтому статус объявления от прогона к прогону не меняется: дальше 30 км, дороже бюджета, подозрительно дёшево, дубликат, нет координат или маршрута, цену нельзя перевести в евро.
- **Id 999.md.** В `listings` хранятся `region_id` и `settlement_id` (номера опций «Регион» и «Населённый пункт»). Одноимённые сёла в разных районах различаются по id. Список разрешённых регионов в настройках остаётся по названиям.
- **Справочник сёл** лежит в `config/settlements.yaml`, ключ — `settlement_id`, отдельной таблицы нет. Сёла вне справочника получают 50 баллов и метку «не изучено».
- **Формат цен.** Проверено 2026-10-30: у всех 1779 объявлений цена фиксированная и указана за весь участок (`mode = PM_FIXED`, `measurement = unit`), цен «за сотку» нет. MDL и USD переводятся в евро по курсу BNM при оценке (`scores.price_eur`).
- **Точки въезда.** 14 точек, где магистрали пересекают границу города Кишинёв (OSM relation 1748490, admin_level 8), лежат в `config/settings.yaml`. Участки в черте города (`settlement_id` 13859) получают 0 км и 0 мин.
- **Координаты.** Если у объявления нет точки на карте, берётся медиана координат объявлений того же села (`coords_approx`). Если таких объявлений нет, участок исключается («нет координат»).
- **Таблицы.** Вместо `enrichment` + `scores` — одна таблица-снимок `scores`, которая перезаписывается при каждой оценке, и кэши `poi`, `route_cache`, `fx_rates`. Дубликаты хранятся в `scores.duplicate_group` / `is_group_primary`.
- **Скрытые объявления** (`user_marks`, команды `/hide` и `/fav`) появятся на этапе 3 вместе с ботом.
- **Коды выхода CLI:** 0 — всё выполнено; 1 — обход прерван; 2 — настройки не прочитаны; 3 — обход прошёл, но оценка или Excel не удались.
- **Excel:** дневная копия называется по дате UTC.
```

- [ ] **Step 7: Проверка внешних сервисов вживую**

Run: `uv run pytest -m live -v`
Expected: 4 passed (999.md, OSRM, BNM, Overpass). Если какой-то упал, сравнить формат ответа с этим планом и сообщить, ничего не меняя в коде источника.

- [ ] **Step 8: Настоящий прогон**

Run: `uv run homealize init-db`
Expected: `База готова: data/homealize.db (схема v2)`.

Run: `uv run homealize run` (дать до 10 минут)
Expected:
- строка `Прогон успешен: увидено ~1780, …`. При первом прогоне после обновления почти все объявления посчитаются «обновлёнными»: у них появились id района и села;
- строка `Оценка: оценено ~1600, в рейтинге N, лучший балл …. Excel: exports\latest.xlsx`;
- код выхода 0.

Если появились «Предупреждение: …», переписать их в отчёт.

Проверить первые строки рейтинга:

Run: `PYTHONIOENCODING=utf-8 uv run python -c "from openpyxl import load_workbook; ws = load_workbook('exports/latest.xlsx')['Топ']; [print([c.value for c in row][:12]) for row in ws.iter_rows(max_row=6)]"`
Expected: заголовок и 5 строк. Балл убывает, цена ≤ 50 000 €, «Км до города» ≤ 30.

Run: `uv run homealize rescore`
Expected: та же оценка без обхода сайта и без новых запросов к OSRM (маршруты уже в кэше); код выхода 0.

- [ ] **Step 9: Commit**

```bash
git add src/homealize/evaluate.py src/homealize/cli.py tests/test_evaluate.py tests/test_cli.py tests/live/test_live_services.py README.md docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md
git commit -m "feat: score after each run; rescore, export and refresh-poi commands"
```
