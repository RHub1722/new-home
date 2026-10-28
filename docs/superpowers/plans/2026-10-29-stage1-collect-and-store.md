# Этап 1 «Сбор и хранение» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** команда `homealize run` проходит поиск 999.md (участки под строительство, 5–10 соток), сохраняет объявления в SQLite, ведёт историю цен, помечает отфильтрованные и снятые с продажи объявления и пишет журнал прогонов.

**Architecture:** Python-пакет `homealize` (src-layout). Адаптер 999.md: вежливый HTTP-клиент (`http.py`), GraphQL-запрос (`query.py`) и разбор ответа в нормализованный `Listing` (`parse.py`), постраничный обход (`source.py`). Хранилище на SQLite с нумерованными SQL-миграциями и функциями-репозиториями. `pipeline.run_once()` связывает источник, фильтры и хранилище, `cli.py` даёт команды `init-db` и `run`.

**Tech Stack:** Python 3.12, uv, httpx, pydantic v2, PyYAML, sqlite3 (stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` (этап 1 из §11; поля 999.md в §3; данные в §5; надёжность в §8; тесты в §9).

## Global Constraints

- Python `>=3.12`, зависимости и запуск только через `uv` (`uv sync`, `uv run …`).
- **Никогда не запрашивать `feature(id: 16)` (контакты продавца) и поле `owner`.** Телефоны в описаниях заменять на `[телефон удалён]` до сохранения (спека §5 «Персональные данные»).
- К 999.md ходим по одному запросу за раз: пауза между запросами случайная 2–3 с, повторы на 429/5xx/таймаут с паузами 5, 15, 45 с; при странице лимита запросов сразу останавливаемся (спека §8).
- Заголовки запросов к 999.md: `lang: ru`, `source: desktop_redesign`, `Accept-Language: ru`, браузерный `User-Agent`.
- Пути по умолчанию: `config/settings.yaml`, `data/homealize.db`, `logs/homealize.log`.
- Время в базе хранится строкой ISO 8601 с часовым поясом (`datetime.isoformat()`), прогон получает `now` снаружи (для тестов).
- Тексты для пользователя (сообщения CLI, логи, ошибки) на русском.
- Объявление снимается (`removed`) только после 2 **полных** проходов подряд без него; оборванный проход ничего не снимает (спека §4 шаг 9).

## Что будет после этого плана

Этапы 2–5 (оценка и Excel, Telegram и расписание, дашборд, VPS) получат **отдельные планы** после завершения этапа 1: они опираются на реальные данные, которые соберёт этот этап. Колонки `settlement_id`, `coords_approx`, `duplicate_group_id` и таблицы `enrichment`, `scores` и т. д. добавятся миграциями `002+`. Ключ `run --dry-run` из спеки §4 появится на этапе 3 вместе с уведомлениями, которые он отключает.

## Структура файлов

```
pyproject.toml                         зависимости, точка входа homealize, настройки pytest
.python-version                        3.12
.gitignore
README.md                              быстрый старт
config/settings.yaml                   настройки по умолчанию
src/homealize/__init__.py
src/homealize/config.py                Settings (pydantic) + load_settings()
src/homealize/models.py                Listing, RunSummary, константы типов автора
src/homealize/filters.py               filter_reason(): жёсткие фильтры этапа 1
src/homealize/sources/__init__.py
src/homealize/sources/base.py          ошибки источника, Page, протокол Source
src/homealize/sources/nine99/__init__.py
src/homealize/sources/nine99/http.py   PoliteClient: паузы, повторы, лимит запросов
src/homealize/sources/nine99/query.py  SEARCH_QUERY + build_search_body()
src/homealize/sources/nine99/parse.py  parse_search_response() → Page, scrub_phones()
src/homealize/sources/nine99/source.py Nine99Source.iter_pages(), build_nine99_source()
src/homealize/storage/__init__.py
src/homealize/storage/db.py            connect(), migrate()
src/homealize/storage/migrations/__init__.py
src/homealize/storage/migrations/001_initial.sql
src/homealize/storage/repo.py          upsert_listing(), mark_unseen(), журнал прогонов
src/homealize/pipeline.py              run_once()
src/homealize/logging_setup.py         configure_logging(), reset_logging()
src/homealize/cli.py                   main(): init-db, run
tests/__init__.py
tests/conftest.py                      фикстура db (SQLite в памяти)
tests/factories.py                     make_listing(), load_fixture(), FakeSource, page()
tests/fixtures/nine99/search_page_1.json
tests/fixtures/nine99/search_page_2.json
tests/test_config.py
tests/test_models.py
tests/test_filters.py
tests/test_pipeline.py
tests/test_cli.py
tests/nine99/__init__.py
tests/nine99/test_http.py
tests/nine99/test_query.py
tests/nine99/test_parse.py
tests/nine99/test_source.py
tests/storage/__init__.py
tests/storage/test_db.py
tests/storage/test_repo.py
tests/live/__init__.py
tests/live/test_live_999.py            смоук-тест против настоящего сайта (pytest -m live)
```

---

### Task 1: Каркас проекта и настройки

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `src/homealize/__init__.py`, `src/homealize/config.py`, `config/settings.yaml`, `tests/__init__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `homealize.config.Settings` с секциями `source: SourceSettings`, `filters: FilterSettings`, `storage: StorageSettings`, `lifecycle: LifecycleSettings`, `logging: LoggingSettings`.
  - `SourceSettings`: `base_url: str`, `page_size: int`, `delay_seconds: tuple[float, float]`, `timeout_seconds: float`, `max_retries: int`, `backoff_seconds: list[float]`, `user_agent: str`.
  - `FilterSettings`: `land_type: str`, `area_sot_min: float`, `area_sot_max: float`, `allowed_regions: list[str]`.
  - `StorageSettings.db_path: Path`; `LifecycleSettings.removal_missed_scans: int`; `LoggingSettings`: `path: Path`, `max_bytes: int`, `backup_count: int`.
  - `homealize.config.load_settings(path: Path) -> Settings`.

- [ ] **Step 1: Создать файлы проекта**

`pyproject.toml`:

```toml
[project]
name = "homealize"
version = "0.1.0"
description = "Мониторинг участков под строительство дома на 999.md"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "pyyaml>=6.0",
]

[project.scripts]
homealize = "homealize.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/homealize"]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not live'"
markers = ["live: обращается к настоящему 999.md, запускать вручную: uv run pytest -m live"]
```

`.python-version`:

```
3.12
```

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
data/*.db
data/*.db-wal
data/*.db-shm
exports/
logs/
```

`src/homealize/__init__.py`:

```python
"""Homealize — мониторинг участков под строительство дома на 999.md."""

__version__ = "0.1.0"
```

`tests/__init__.py`: пустой файл.

- [ ] **Step 2: Установить окружение**

Run: `uv sync`
Expected: создана `.venv`, установлены httpx, pydantic, pyyaml, pytest; появился `uv.lock`.

- [ ] **Step 3: Написать падающий тест настроек**

`tests/test_config.py`:

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from homealize.config import Settings, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_match_spec():
    s = Settings()
    assert (s.filters.area_sot_min, s.filters.area_sot_max) == (5, 10)
    assert s.filters.land_type == "Участок под строительство"
    assert "Кишинёв мун." in s.filters.allowed_regions
    assert s.source.delay_seconds == (2.0, 3.0)
    assert s.source.backoff_seconds == [5.0, 15.0, 45.0]
    assert s.lifecycle.removal_missed_scans == 2


def test_load_settings_overrides_defaults(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "source:\n  page_size: 10\nfilters:\n  allowed_regions: ['Яловены']\n",
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert s.source.page_size == 10
    assert s.filters.allowed_regions == ["Яловены"]
    assert s.filters.area_sot_max == 10


def test_empty_file_gives_defaults(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("", encoding="utf-8")
    assert load_settings(cfg) == Settings()


def test_unknown_key_is_rejected(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("filters:\n  area_max: 12\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(cfg)


def test_inverted_area_range_is_rejected():
    with pytest.raises(ValidationError):
        Settings.model_validate({"filters": {"area_sot_min": 10, "area_sot_max": 5}})


def test_too_few_backoff_pauses_are_rejected():
    with pytest.raises(ValidationError):
        Settings.model_validate({"source": {"max_retries": 3, "backoff_seconds": [5]}})


def test_project_settings_file_is_valid():
    s = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    assert s.filters.area_sot_min == 5
    assert s.storage.db_path == Path("data/homealize.db")
```

- [ ] **Step 4: Убедиться, что тест падает**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'homealize.config'`.

- [ ] **Step 5: Реализовать настройки**

`src/homealize/config.py`:

```python
"""Настройки Homealize: читаются из config/settings.yaml и проверяются pydantic."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class Settings(_Section):
    source: SourceSettings = Field(default_factory=SourceSettings)
    filters: FilterSettings = Field(default_factory=FilterSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    lifecycle: LifecycleSettings = Field(default_factory=LifecycleSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


def load_settings(path: Path) -> Settings:
    """Читает YAML-файл настроек; отсутствующие ключи берутся по умолчанию."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)
```

`config/settings.yaml`:

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
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_config.py -v`
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore src/homealize/__init__.py src/homealize/config.py config/settings.yaml tests/__init__.py tests/test_config.py
git commit -m "feat: project scaffold and settings"
```

---

### Task 2: Модель объявления и жёсткие фильтры

**Files:**
- Create: `src/homealize/models.py`, `src/homealize/filters.py`, `tests/factories.py`
- Test: `tests/test_models.py`, `tests/test_filters.py`

**Interfaces:**
- Consumes: `homealize.config.FilterSettings` (Task 1).
- Produces:
  - `homealize.models.Listing` — frozen dataclass: `id: str`, `source: str`, `url: str`, `title: str`, `author_type: str`, `region`, `settlement`, `zone`, `street`, `land_type`, `currency`, `description: str | None`, `lat`, `lon`, `area_sot`, `price_original: float | None`, `has_gas`, `has_electricity`, `has_water`, `has_sewerage`, `has_asphalt_road`, `in_settlement: bool | None`, `images: tuple[str, ...]`, `site_updated_at: datetime | None`; свойства `price_eur -> float | None`, `price_per_sot_eur -> float | None`.
  - Константы `AUTHOR_PRIVATE = "private"`, `AUTHOR_AGENCY = "agency"`, `AUTHOR_DEVELOPER = "developer"`, `AUTHOR_UNKNOWN = "unknown"`.
  - `homealize.models.RunSummary` — dataclass: `search_complete: bool`, `ads_seen`, `new`, `changed`, `price_changed`, `removed: int`, `errors: list[str]`; свойство `status -> "ok" | "failed"`.
  - `homealize.filters.filter_reason(listing: Listing, rules: FilterSettings) -> str | None` — `"land_type"`, `"area"`, `"region"`, `"no_price"` или `None`.
  - `tests.factories.make_listing(**overrides) -> Listing`, `tests.factories.load_fixture(name: str) -> dict`.

- [ ] **Step 1: Создать помощники для тестов**

`tests/factories.py`:

```python
"""Помощники для тестов: готовые объявления и фикстуры 999.md."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homealize.models import Listing

FIXTURES = Path(__file__).parent / "fixtures"
CHISINAU_TZ = timezone(timedelta(hours=2))


def make_listing(**overrides) -> Listing:
    """Корректное объявление, проходящее все фильтры; поля можно переопределить."""
    fields = {
        "id": "100000001",
        "source": "999",
        "url": "https://999.md/ru/100000001",
        "title": "6 сот., участок под строительство",
        "author_type": "private",
        "region": "Кишинёв мун.",
        "settlement": "Гратиешты",
        "zone": "Окраина",
        "street": None,
        "lat": 47.09921,
        "lon": 28.8165,
        "area_sot": 6.0,
        "land_type": "Участок под строительство",
        "price_original": 28000.0,
        "currency": "EUR",
        "has_gas": True,
        "has_electricity": True,
        "has_water": True,
        "has_sewerage": False,
        "has_asphalt_road": False,
        "in_settlement": True,
        "description": "Ровный участок.",
        "images": ("https://i.simpalsmedia.com/999.md/BoardImages/900x900/aaa111.jpg",),
        "site_updated_at": datetime(2026, 10, 28, 23, 15, tzinfo=CHISINAU_TZ),
    }
    fields.update(overrides)
    return Listing(**fields)


def load_fixture(name: str) -> dict:
    """Читает JSON-фикстуру ответа 999.md из tests/fixtures/nine99/."""
    return json.loads((FIXTURES / "nine99" / name).read_text(encoding="utf-8"))
```

- [ ] **Step 2: Написать падающие тесты**

`tests/test_models.py`:

```python
from homealize.models import RunSummary
from tests.factories import make_listing


def test_price_eur_for_euro_listing():
    assert make_listing().price_eur == 28000.0


def test_price_eur_is_none_for_other_currency():
    assert make_listing(price_original=900000.0, currency="MDL").price_eur is None


def test_price_per_sot_is_rounded_to_cents():
    assert make_listing().price_per_sot_eur == 4666.67


def test_price_per_sot_needs_area_and_euro_price():
    assert make_listing(area_sot=None).price_per_sot_eur is None
    assert make_listing(area_sot=0.0).price_per_sot_eur is None
    assert make_listing(price_original=None, currency=None).price_per_sot_eur is None


def test_run_summary_status():
    assert RunSummary(search_complete=True).status == "ok"
    assert RunSummary().status == "failed"
```

`tests/test_filters.py`:

```python
import pytest

from homealize.config import FilterSettings
from homealize.filters import filter_reason
from tests.factories import make_listing

RULES = FilterSettings()


def test_good_listing_passes():
    assert filter_reason(make_listing(), RULES) is None


def test_wrong_land_type():
    assert filter_reason(make_listing(land_type="Участок под с/х"), RULES) == "land_type"


@pytest.mark.parametrize("area", [5.0, 7.5, 10.0])
def test_area_bounds_are_inclusive(area):
    assert filter_reason(make_listing(area_sot=area), RULES) is None


@pytest.mark.parametrize("area", [4.9, 10.1, 12.0, None])
def test_area_outside_range(area):
    assert filter_reason(make_listing(area_sot=area), RULES) == "area"


def test_region_outside_list():
    assert filter_reason(make_listing(region="Кагул"), RULES) == "region"


def test_unknown_region_passes():
    assert filter_reason(make_listing(region=None), RULES) is None


def test_missing_price():
    assert filter_reason(make_listing(price_original=None, currency=None), RULES) == "no_price"
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_models.py tests/test_filters.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'homealize.models'`.

- [ ] **Step 4: Реализовать модели и фильтры**

`src/homealize/models.py`:

```python
"""Доменные модели Homealize, общие для всех модулей."""

from dataclasses import dataclass, field
from datetime import datetime

AUTHOR_PRIVATE = "private"
AUTHOR_AGENCY = "agency"
AUTHOR_DEVELOPER = "developer"
AUTHOR_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Listing:
    """Объявление в нормализованном виде, не зависящем от сайта-источника.

    Булевы признаки трёхзначные: True — отмечено, False — не отмечено,
    None — продавец раздел «Дополнительно» не заполнял.
    """

    id: str
    source: str
    url: str
    title: str
    author_type: str = AUTHOR_UNKNOWN
    region: str | None = None
    settlement: str | None = None
    zone: str | None = None
    street: str | None = None
    lat: float | None = None
    lon: float | None = None
    area_sot: float | None = None
    land_type: str | None = None
    price_original: float | None = None
    currency: str | None = None
    has_gas: bool | None = None
    has_electricity: bool | None = None
    has_water: bool | None = None
    has_sewerage: bool | None = None
    has_asphalt_road: bool | None = None
    in_settlement: bool | None = None
    description: str | None = None
    images: tuple[str, ...] = ()
    site_updated_at: datetime | None = None

    @property
    def price_eur(self) -> float | None:
        """Цена в евро; для других валют None до конвертации по курсу BNM (этап 2)."""
        return self.price_original if self.currency == "EUR" else None

    @property
    def price_per_sot_eur(self) -> float | None:
        if self.price_eur is None or not self.area_sot:
            return None
        return round(self.price_eur / self.area_sot, 2)


@dataclass
class RunSummary:
    """Итог одного прогона: что увидели, что изменилось, какие были ошибки."""

    search_complete: bool = False
    ads_seen: int = 0
    new: int = 0
    changed: int = 0
    price_changed: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if self.search_complete else "failed"
```

`src/homealize/filters.py`:

```python
"""Жёсткие фильтры этапа 1: что вообще считается подходящим участком."""

from homealize.config import FilterSettings
from homealize.models import Listing


def filter_reason(listing: Listing, rules: FilterSettings) -> str | None:
    """Причина, по которой объявление не проходит фильтры, или None, если проходит."""
    if listing.land_type != rules.land_type:
        return "land_type"
    if listing.area_sot is None or not rules.area_sot_min <= listing.area_sot <= rules.area_sot_max:
        return "area"
    if listing.region is not None and listing.region not in rules.allowed_regions:
        return "region"
    if listing.price_original is None:
        return "no_price"
    return None
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_models.py tests/test_filters.py -v`
Expected: 17 passed.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/models.py src/homealize/filters.py tests/factories.py tests/test_models.py tests/test_filters.py
git commit -m "feat: listing model and hard filters"
```

---

### Task 3: Вежливый HTTP-клиент

**Files:**
- Create: `src/homealize/sources/__init__.py`, `src/homealize/sources/base.py`, `src/homealize/sources/nine99/__init__.py`, `src/homealize/sources/nine99/http.py`, `tests/nine99/__init__.py`
- Test: `tests/nine99/test_http.py`

**Interfaces:**
- Consumes: `homealize.models.Listing` (Task 2).
- Produces:
  - Ошибки в `homealize.sources.base`: `SourceError` (базовая), `SourceUnavailableError`, `RateLimitedError`, `SourceSchemaError`, `EmptySearchError`.
  - `homealize.sources.base.Page` — frozen dataclass: `listings: list[Listing]`, `total_count: int`, `parse_errors: list[str]`.
  - `homealize.sources.base.Source` — Protocol: атрибут `name: str`, методы `iter_pages() -> Iterator[Page]`, `close() -> None`.
  - `homealize.sources.nine99.http.PoliteClient(base_url, *, headers, delay_seconds, timeout_seconds, backoff_seconds, max_retries, transport=None, sleep=time.sleep, uniform=random.uniform)`; методы `post_json(path: str, payload: dict) -> dict`, `close() -> None`.

- [ ] **Step 1: Создать пакеты**

`src/homealize/sources/__init__.py`:

```python
"""Источники объявлений (сайты)."""
```

`src/homealize/sources/nine99/__init__.py`:

```python
"""Адаптер 999.md."""
```

`tests/nine99/__init__.py`: пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/nine99/test_http.py`:

```python
import httpx
import pytest

from homealize.sources.base import RateLimitedError, SourceSchemaError, SourceUnavailableError
from homealize.sources.nine99.http import PoliteClient

OK = {"data": {"ok": True}}


def make_client(responses, *, max_retries=3, backoff=(5.0, 15.0, 45.0)):
    """Клиент с поддельным транспортом: отдаёт ответы по очереди, последний повторяет."""
    requests, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        item = responses[min(len(requests), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    client = PoliteClient(
        "https://999.md",
        headers={"lang": "ru"},
        delay_seconds=(2.0, 3.0),
        timeout_seconds=5,
        backoff_seconds=list(backoff),
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        uniform=lambda low, high: low,
    )
    return client, requests, sleeps


def test_returns_json_and_sends_headers():
    client, requests, sleeps = make_client([httpx.Response(200, json=OK)])
    assert client.post_json("/graphql", {"query": "q"}) == OK
    assert requests[0].method == "POST"
    assert requests[0].url == "https://999.md/graphql"
    assert requests[0].headers["lang"] == "ru"
    assert sleeps == []


def test_pauses_between_requests_but_not_before_first():
    client, _, sleeps = make_client([httpx.Response(200, json=OK)])
    client.post_json("/graphql", {})
    client.post_json("/graphql", {})
    assert sleeps == [2.0]


def test_retries_on_server_error_then_succeeds():
    client, requests, sleeps = make_client(
        [httpx.Response(503), httpx.Response(503), httpx.Response(200, json=OK)]
    )
    assert client.post_json("/graphql", {}) == OK
    assert len(requests) == 3
    assert sleeps == [5.0, 2.0, 15.0, 2.0]


def test_retries_on_timeout():
    client, requests, _ = make_client(
        [httpx.ConnectTimeout("timeout"), httpx.Response(200, json=OK)]
    )
    assert client.post_json("/graphql", {}) == OK
    assert len(requests) == 2


def test_gives_up_after_max_retries():
    client, requests, _ = make_client([httpx.Response(429)], max_retries=2, backoff=(1.0, 2.0))
    with pytest.raises(SourceUnavailableError, match="HTTP 429"):
        client.post_json("/graphql", {})
    assert len(requests) == 3


def test_rate_limit_page_stops_immediately():
    page = httpx.Response(200, text="<html><a href='/999.md_request_limit.html'>limit</a></html>")
    client, requests, _ = make_client([page])
    with pytest.raises(RateLimitedError):
        client.post_json("/graphql", {})
    assert len(requests) == 1


def test_non_json_response_is_schema_error():
    client, _, _ = make_client([httpx.Response(200, text="<html>oops</html>")])
    with pytest.raises(SourceSchemaError):
        client.post_json("/graphql", {})


def test_client_error_status_is_schema_error():
    client, requests, _ = make_client([httpx.Response(400, json={"errors": [{"message": "bad"}]})])
    with pytest.raises(SourceSchemaError, match="400"):
        client.post_json("/graphql", {})
    assert len(requests) == 1
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/nine99/test_http.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'homealize.sources.base'`.

- [ ] **Step 4: Реализовать базовые типы источника**

`src/homealize/sources/base.py`:

```python
"""Общий интерфейс источника объявлений и его ошибки."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from homealize.models import Listing


class SourceError(Exception):
    """Базовая ошибка источника объявлений."""


class SourceUnavailableError(SourceError):
    """Сайт не ответил нормально после всех повторов."""


class RateLimitedError(SourceError):
    """Сайт показал страницу превышения лимита запросов."""


class SourceSchemaError(SourceError):
    """Ответ сайта не похож на ожидаемый: вероятно, сайт что-то поменял."""


class EmptySearchError(SourceError):
    """Поиск вернул 0 объявлений: вероятно, сайт что-то поменял."""


@dataclass(frozen=True)
class Page:
    """Одна страница результатов поиска."""

    listings: list[Listing]
    total_count: int
    parse_errors: list[str] = field(default_factory=list)


class Source(Protocol):
    name: str

    def iter_pages(self) -> Iterator[Page]: ...

    def close(self) -> None: ...
```

- [ ] **Step 5: Реализовать клиент**

`src/homealize/sources/nine99/http.py`:

```python
"""HTTP-клиент, который ходит на сайт бережно: по одному запросу, с паузами и повторами."""

import logging
import random
import time
from collections.abc import Callable

import httpx

from homealize.sources.base import RateLimitedError, SourceSchemaError, SourceUnavailableError

log = logging.getLogger(__name__)

RATE_LIMIT_MARKER = "request_limit"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class PoliteClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str],
        delay_seconds: tuple[float, float],
        timeout_seconds: float,
        backoff_seconds: list[float],
        max_retries: int,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=True,
        )
        self._delay = delay_seconds
        self._backoff = backoff_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._uniform = uniform
        self._requests_made = 0

    def post_json(self, path: str, payload: dict) -> dict:
        """POST с JSON-телом; возвращает разобранный JSON-объект ответа."""
        attempt = 0
        while True:
            self._pause_between_requests()
            try:
                response = self._client.post(path, json=payload)
            except httpx.TransportError as exc:
                failure = f"{type(exc).__name__}: {exc}"
            else:
                if RATE_LIMIT_MARKER in str(response.url) or RATE_LIMIT_MARKER in response.text[:2000]:
                    raise RateLimitedError(f"999.md ограничил частоту запросов ({response.url})")
                if response.status_code not in RETRY_STATUSES:
                    return self._decode(response)
                failure = f"HTTP {response.status_code}"
            if attempt >= self._max_retries:
                raise SourceUnavailableError(f"{path}: {failure} после {attempt + 1} попыток")
            wait = self._backoff[attempt]
            log.warning("%s: %s, повтор через %s с", path, failure, wait)
            self._sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._client.close()

    def _pause_between_requests(self) -> None:
        if self._requests_made:
            self._sleep(self._uniform(*self._delay))
        self._requests_made += 1

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        if response.status_code != 200:
            raise SourceSchemaError(f"неожиданный ответ HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceSchemaError(f"ответ не является JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SourceSchemaError("JSON-ответ не является объектом")
        return data
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `uv run pytest tests/nine99/test_http.py -v`
Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
git add src/homealize/sources tests/nine99/__init__.py tests/nine99/test_http.py
git commit -m "feat: polite HTTP client with retries and rate-limit detection"
```

---

### Task 4: GraphQL-запрос и разбор ответа 999.md

**Files:**
- Create: `src/homealize/sources/nine99/query.py`, `src/homealize/sources/nine99/parse.py`, `tests/fixtures/nine99/search_page_1.json`, `tests/fixtures/nine99/search_page_2.json`
- Test: `tests/nine99/test_query.py`, `tests/nine99/test_parse.py`

**Interfaces:**
- Consumes: `Listing`, константы `AUTHOR_*` (Task 2); `Page`, `SourceSchemaError` (Task 3); `tests.factories.load_fixture` (Task 2).
- Produces:
  - `homealize.sources.nine99.query.SEARCH_QUERY: str`.
  - `homealize.sources.nine99.query.build_search_body(*, skip: int, limit: int, area_min: float, area_max: float) -> dict`.
  - `homealize.sources.nine99.parse.parse_search_response(payload: dict) -> Page`.
  - `homealize.sources.nine99.parse.scrub_phones(text: str) -> str`.
  - Константы `IMAGE_URL`, `AD_URL`, `PHONE_PLACEHOLDER = "[телефон удалён]"`.

Структура ответа и номера полей описаны в спеке §3 «Поля объявления». Фикстуры синтетические, но повторяют реальную структуру ответа (снята 2026-10-29); телефоны в них заведомо фиктивные.

- [ ] **Step 1: Создать фикстуры**

`tests/fixtures/nine99/search_page_1.json`:

```json
{
  "data": {
    "searchAds": {
      "count": 6,
      "ads": [
        {
          "id": "100000001",
          "title": "6 сот., участок под строительство",
          "reseted": "2026-10-28T23:15:00+02:00",
          "price": {"value": {"bargain": false, "down_payment": 0, "measurement": "UNIT_EUR", "mode": "PM_FIXED", "unit": "UNIT_EUR", "value": 28000}},
          "images": {"value": ["aaa111.jpg", "bbb222.jpg"]},
          "body": {"value": {"ro": "Teren drept.", "ru": "Ровный участок, все коммуникации. Звоните +373 60 000 000", "translated": "Ровный участок, все коммуникации. Звоните +373 60 000 000"}},
          "author": {"value": {"abbreviations": {}, "translated": "Частное лицо", "value": 18895}},
          "landType": {"value": {"abbreviations": {}, "translated": "Участок под строительство", "value": 1039}},
          "landArea": {"value": {"unit": "UNIT_ARE", "value": 6}},
          "mapPoint": {"value": {"lat": 47.09921, "lon": 28.8165, "zoom": 16}},
          "region": {"value": {"abbreviations": {}, "translated": "Кишинёв мун.", "value": 12900}},
          "city": {"value": {"abbreviations": {}, "translated": "Гратиешты", "value": 14010}},
          "district": {"value": {"abbreviations": {}, "translated": "Окраина", "value": 17204}},
          "street": {"value": "-"},
          "gas": {"value": true},
          "electricity": {"value": true},
          "water": {"value": true},
          "sewerage": {"value": false},
          "asphaltRoad": {"value": false},
          "inSettlement": {"value": true}
        },
        {
          "id": "100000002",
          "title": "8 сот., участок под строительство",
          "reseted": "2026-10-28T20:00:00+02:00",
          "price": {"value": {"bargain": false, "down_payment": 0, "measurement": "UNIT_MDL", "mode": "PM_FIXED", "unit": "UNIT_MDL", "value": 900000}},
          "images": {"value": []},
          "body": null,
          "author": {"value": {"abbreviations": {}, "translated": "Агентство", "value": 18894}},
          "landType": {"value": {"abbreviations": {}, "translated": "Участок под строительство", "value": 1039}},
          "landArea": {"value": {"unit": "UNIT_ARE", "value": 8}},
          "mapPoint": null,
          "region": {"value": {"abbreviations": {}, "translated": "Яловены", "value": 12920}},
          "city": {"value": {"abbreviations": {}, "translated": "Яловены", "value": 13700}},
          "district": {"value": {"abbreviations": {}, "translated": "Центр", "value": 15000}},
          "street": {"value": "str. Alexandru cel Bun"},
          "gas": null,
          "electricity": null,
          "water": null,
          "sewerage": null,
          "asphaltRoad": null,
          "inSettlement": null
        },
        {
          "id": "100000003",
          "title": "7 сот., участок под строительство",
          "reseted": "2026-10-28T18:30:00+02:00",
          "price": {"value": {"bargain": false, "down_payment": 0, "measurement": "UNIT_EUR", "mode": "PM_FIXED", "unit": "UNIT_EUR", "value": 15000}},
          "images": {"value": ["ddd444.jpg"]},
          "body": {"value": {"ro": null, "ru": "Участок в Кагуле.", "translated": "Участок в Кагуле."}},
          "author": {"value": {"abbreviations": {}, "translated": "Застройщик", "value": 20364}},
          "landType": {"value": {"abbreviations": {}, "translated": "Участок под строительство", "value": 1039}},
          "landArea": {"value": {"unit": "UNIT_ARE", "value": 7}},
          "mapPoint": {"value": {"lat": 45.9075, "lon": 28.1944, "zoom": 16}},
          "region": {"value": {"abbreviations": {}, "translated": "Кагул", "value": 12990}},
          "city": {"value": {"abbreviations": {}, "translated": "Кагул", "value": 13100}},
          "district": {"value": {"abbreviations": {}, "translated": "Центр", "value": 15001}},
          "street": {"value": "-"},
          "gas": {"value": false},
          "electricity": {"value": false},
          "water": {"value": false},
          "sewerage": {"value": false},
          "asphaltRoad": {"value": false},
          "inSettlement": {"value": true}
        },
        {
          "id": "100000004",
          "title": "12 сот., участок под строительство",
          "reseted": "2026-10-28T15:00:00+02:00",
          "price": {"value": {"bargain": false, "down_payment": 0, "measurement": "UNIT_EUR", "mode": "PM_FIXED", "unit": "UNIT_EUR", "value": 40000}},
          "images": {"value": ["eee555.jpg"]},
          "body": {"value": {"ro": null, "ru": "Большой участок.", "translated": "Большой участок."}},
          "author": {"value": {"abbreviations": {}, "translated": "Частное лицо", "value": 18895}},
          "landType": {"value": {"abbreviations": {}, "translated": "Участок под строительство", "value": 1039}},
          "landArea": {"value": {"unit": "UNIT_ARE", "value": 12}},
          "mapPoint": {"value": {"lat": 46.9876, "lon": 28.9532, "zoom": 16}},
          "region": {"value": {"abbreviations": {}, "translated": "Кишинёв мун.", "value": 12900}},
          "city": {"value": {"abbreviations": {}, "translated": "Колоница", "value": 13915}},
          "district": {"value": {"abbreviations": {}, "translated": "Окраина", "value": 17300}},
          "street": {"value": "-"},
          "gas": {"value": true},
          "electricity": {"value": true},
          "water": {"value": false},
          "sewerage": {"value": false},
          "asphaltRoad": {"value": true},
          "inSettlement": {"value": true}
        }
      ]
    }
  }
}
```

`tests/fixtures/nine99/search_page_2.json`:

```json
{
  "data": {
    "searchAds": {
      "count": 6,
      "ads": [
        {
          "id": "100000005",
          "title": "5 сот., участок под строительство",
          "reseted": "2026-10-27T12:00:00+02:00",
          "price": {"value": {"bargain": false, "down_payment": 0, "measurement": "UNIT_EUR", "mode": "PM_FIXED", "unit": "UNIT_EUR", "value": 30000}},
          "images": {"value": []},
          "body": null,
          "author": {"value": {"abbreviations": {}, "translated": "Частное лицо", "value": 18895}},
          "landType": {"value": {"abbreviations": {}, "translated": "Участок под строительство", "value": 1039}},
          "landArea": {"value": {"unit": "UNIT_ARE", "value": 5}},
          "mapPoint": {"value": {"lat": "north", "lon": 28.9, "zoom": 16}},
          "region": {"value": {"abbreviations": {}, "translated": "Кишинёв мун.", "value": 12900}},
          "city": {"value": {"abbreviations": {}, "translated": "Бачой", "value": 13800}},
          "district": {"value": {"abbreviations": {}, "translated": "Центр", "value": 15002}},
          "street": {"value": "-"},
          "gas": null,
          "electricity": null,
          "water": null,
          "sewerage": null,
          "asphaltRoad": null,
          "inSettlement": null
        },
        {
          "id": "100000006",
          "title": "9 сот., участок под строительство",
          "reseted": "2026-10-27T10:00:00+02:00",
          "price": null,
          "images": {"value": ["ccc333.jpg"]},
          "body": {"value": {"ro": null, "ru": "Участок у леса.", "translated": "Участок у леса."}},
          "author": {"value": {"abbreviations": {}, "translated": "Частное лицо", "value": 18895}},
          "landType": {"value": {"abbreviations": {}, "translated": "Участок под строительство", "value": 1039}},
          "landArea": {"value": {"unit": "UNIT_ARE", "value": 9}},
          "mapPoint": {"value": {"lat": 46.9431, "lon": 28.779, "zoom": 16}},
          "region": {"value": {"abbreviations": {}, "translated": "Яловены", "value": 12920}},
          "city": {"value": {"abbreviations": {}, "translated": "Яловены", "value": 13700}},
          "district": {"value": {"abbreviations": {}, "translated": "Окраина", "value": 15003}},
          "street": {"value": "str. Renașterii"},
          "gas": {"value": false},
          "electricity": {"value": true},
          "water": {"value": false},
          "sewerage": {"value": false},
          "asphaltRoad": {"value": true},
          "inSettlement": {"value": true}
        }
      ]
    }
  }
}
```

- [ ] **Step 2: Написать падающие тесты запроса**

`tests/nine99/test_query.py`:

```python
from homealize.sources.nine99.query import SEARCH_QUERY, build_search_body


def test_search_body_has_filters_and_pagination():
    body = build_search_body(skip=78, limit=78, area_min=5, area_max=10)
    search = body["variables"]["input"]
    assert body["operationName"] == "SearchAds"
    assert body["query"] == SEARCH_QUERY
    assert body["variables"]["locale"] == "ru_RU"
    assert search["pagination"] == {"limit": 78, "skip": 78}
    assert search["subCategoryId"] == 1407
    assert search["sort"] == "SORT_ADS_DATE_DESC"
    offer, land_type, area = search["filters"]
    assert offer == {"filterId": 16, "features": [{"featureId": 1, "optionIds": [776]}]}
    assert land_type == {"filterId": 42, "features": [{"featureId": 258, "optionIds": [1039]}]}
    assert area["filterId"] == 1211
    assert area["features"][0] == {"featureId": 245, "range": {"min": "5", "max": "10"}, "unit": "UNIT_ARE"}


def test_fractional_area_bounds_are_kept():
    body = build_search_body(skip=0, limit=10, area_min=5.5, area_max=10)
    area = body["variables"]["input"]["filters"][2]
    assert area["features"][0]["range"] == {"min": "5.5", "max": "10"}


def test_query_never_requests_seller_contacts():
    assert "feature(id: 16)" not in SEARCH_QUERY
    assert "owner" not in SEARCH_QUERY
    assert "phone" not in SEARCH_QUERY.lower()
```

- [ ] **Step 3: Написать падающие тесты разбора**

`tests/nine99/test_parse.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from homealize.sources.base import SourceSchemaError
from homealize.sources.nine99.parse import IMAGE_URL, parse_search_response, scrub_phones
from tests.factories import load_fixture

CHISINAU = timezone(timedelta(hours=2))


def by_id(name: str) -> dict:
    return {listing.id: listing for listing in parse_search_response(load_fixture(name)).listings}


def test_page_1_parses_all_ads_without_errors():
    page = parse_search_response(load_fixture("search_page_1.json"))
    assert page.total_count == 6
    assert [listing.id for listing in page.listings] == ["100000001", "100000002", "100000003", "100000004"]
    assert page.parse_errors == []


def test_full_ad_is_normalized():
    ad = by_id("search_page_1.json")["100000001"]
    assert ad.source == "999"
    assert ad.url == "https://999.md/ru/100000001"
    assert ad.title == "6 сот., участок под строительство"
    assert ad.author_type == "private"
    assert (ad.region, ad.settlement, ad.zone) == ("Кишинёв мун.", "Гратиешты", "Окраина")
    assert ad.street is None
    assert (ad.lat, ad.lon) == (47.09921, 28.8165)
    assert ad.area_sot == 6.0
    assert ad.land_type == "Участок под строительство"
    assert (ad.price_original, ad.currency, ad.price_eur) == (28000.0, "EUR", 28000.0)
    assert (ad.has_gas, ad.has_electricity, ad.has_water, ad.has_sewerage) == (True, True, True, False)
    assert (ad.has_asphalt_road, ad.in_settlement) == (False, True)
    assert ad.images == (IMAGE_URL.format(name="aaa111.jpg"), IMAGE_URL.format(name="bbb222.jpg"))
    assert ad.site_updated_at == datetime(2026, 10, 28, 23, 15, tzinfo=CHISINAU)


def test_phone_in_description_is_removed():
    ad = by_id("search_page_1.json")["100000001"]
    assert ad.description == "Ровный участок, все коммуникации. Звоните [телефон удалён]"


def test_ad_without_map_and_utilities_section():
    ad = by_id("search_page_1.json")["100000002"]
    assert ad.author_type == "agency"
    assert (ad.lat, ad.lon) == (None, None)
    assert (ad.has_gas, ad.has_electricity, ad.has_water, ad.has_sewerage) == (None, None, None, None)
    assert ad.in_settlement is None
    assert ad.street == "str. Alexandru cel Bun"
    assert ad.description is None
    assert ad.images == ()
    assert (ad.price_original, ad.currency, ad.price_eur) == (900000.0, "MDL", None)


def test_developer_author():
    assert by_id("search_page_1.json")["100000003"].author_type == "developer"


def test_broken_ad_is_reported_and_others_kept():
    page = parse_search_response(load_fixture("search_page_2.json"))
    assert [listing.id for listing in page.listings] == ["100000006"]
    assert len(page.parse_errors) == 1
    assert "100000005" in page.parse_errors[0]


def test_missing_price_is_none():
    ad = by_id("search_page_2.json")["100000006"]
    assert (ad.price_original, ad.currency) == (None, None)


def test_unknown_area_unit_gives_no_area():
    payload = load_fixture("search_page_1.json")
    payload["data"]["searchAds"]["ads"][0]["landArea"]["value"]["unit"] = "UNIT_HECTARE"
    assert parse_search_response(payload).listings[0].area_sot is None


def test_graphql_errors_raise_schema_error():
    with pytest.raises(SourceSchemaError, match="GraphQL"):
        parse_search_response({"errors": [{"message": "Cannot query field"}], "data": None})


def test_unexpected_structure_raises_schema_error():
    with pytest.raises(SourceSchemaError):
        parse_search_response({"data": {"somethingElse": {}}})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Звоните +373 60 000 000", "Звоните [телефон удалён]"),
        ("тел. 060000000, Иван", "тел. [телефон удалён], Иван"),
        ("тел 079-00-00-00", "тел [телефон удалён]"),
        ("Цена 45 000 €, торг", "Цена 45 000 €, торг"),
        ("Участок 8 соток, 2026 год", "Участок 8 соток, 2026 год"),
        ("Цена 1 200 000 MDL", "Цена 1 200 000 MDL"),
    ],
)
def test_scrub_phones(text, expected):
    assert scrub_phones(text) == expected
```

- [ ] **Step 4: Убедиться, что тесты падают**

Run: `uv run pytest tests/nine99/test_query.py tests/nine99/test_parse.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'homealize.sources.nine99.query'`.

- [ ] **Step 5: Реализовать запрос**

`src/homealize/sources/nine99/query.py`:

```python
"""GraphQL-запрос поиска участков на 999.md.

Номера полей сняты с сайта 2026-10-29 (спека §3). Поле feature(id: 16), контакты
продавца, и поле owner намеренно НЕ запрашиваются.
"""

SEARCH_QUERY = """
query SearchAds($input: Ads_SearchInput!, $locale: Common_Locale) {
  searchAds(input: $input) {
    ads {
      id
      title
      reseted(input: {format: "2006-01-02T15:04:05Z07:00", locale: $locale, timezone: "Europe/Chisinau", getDiff: false})
      price: feature(id: 2) { value }
      images: feature(id: 14) { value }
      body: feature(id: 13) { value }
      author: feature(id: 795) { value }
      landType: feature(id: 258) { value }
      landArea: feature(id: 245) { value }
      mapPoint: feature(id: 3) { value }
      region: feature(id: 7) { value }
      city: feature(id: 8) { value }
      district: feature(id: 9) { value }
      street: feature(id: 10) { value }
      gas: feature(id: 168) { value }
      electricity: feature(id: 236) { value }
      water: feature(id: 165) { value }
      sewerage: feature(id: 181) { value }
      asphaltRoad: feature(id: 155) { value }
      inSettlement: feature(id: 209) { value }
    }
    count
  }
}
"""

LAND_SUBCATEGORY_ID = 1407
OFFER_SELL = {"filterId": 16, "features": [{"featureId": 1, "optionIds": [776]}]}
LAND_FOR_CONSTRUCTION = {"filterId": 42, "features": [{"featureId": 258, "optionIds": [1039]}]}


def build_search_body(*, skip: int, limit: int, area_min: float, area_max: float) -> dict:
    """Тело POST /graphql для одной страницы поиска (новые объявления первыми)."""
    area = {
        "filterId": 1211,
        "features": [
            {"featureId": 245, "range": {"min": _number(area_min), "max": _number(area_max)}, "unit": "UNIT_ARE"}
        ],
    }
    return {
        "operationName": "SearchAds",
        "variables": {
            "input": {
                "source": "AD_SOURCE_DESKTOP_REDESIGN",
                "sort": "SORT_ADS_DATE_DESC",
                "pagination": {"limit": limit, "skip": skip},
                "subCategoryId": LAND_SUBCATEGORY_ID,
                "filters": [OFFER_SELL, LAND_FOR_CONSTRUCTION, area],
            },
            "locale": "ru_RU",
        },
        "query": SEARCH_QUERY,
    }


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
```

- [ ] **Step 6: Реализовать разбор**

`src/homealize/sources/nine99/parse.py`:

```python
"""Разбор ответа SearchAds 999.md в нормализованные объявления Listing."""

import re
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from homealize.models import AUTHOR_AGENCY, AUTHOR_DEVELOPER, AUTHOR_PRIVATE, AUTHOR_UNKNOWN, Listing
from homealize.sources.base import Page, SourceSchemaError

SOURCE_NAME = "999"
AD_URL = "https://999.md/ru/{id}"
IMAGE_URL = "https://i.simpalsmedia.com/999.md/BoardImages/900x900/{name}"
AUTHOR_TYPES = {18895: AUTHOR_PRIVATE, 18894: AUTHOR_AGENCY, 20364: AUTHOR_DEVELOPER}
PHONE_PLACEHOLDER = "[телефон удалён]"
_PHONE_CANDIDATE = re.compile(r"\+?\d[\d\s\-().]{6,}\d")

T = TypeVar("T")


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Feature(_Raw, Generic[T]):
    value: T | None = None


class OptionValue(_Raw):
    translated: str
    value: int


class PriceValue(_Raw):
    value: float | None = None
    unit: str | None = None
    mode: str | None = None


class AreaValue(_Raw):
    value: float
    unit: str


class MapPointValue(_Raw):
    lat: float
    lon: float


class BodyValue(_Raw):
    translated: str | None = None
    ru: str | None = None
    ro: str | None = None


class RawAd(_Raw):
    id: str
    title: str
    reseted: datetime | None = None
    price: Feature[PriceValue] | None = None
    images: Feature[list[str]] | None = None
    body: Feature[BodyValue] | None = None
    author: Feature[OptionValue] | None = None
    landType: Feature[OptionValue] | None = None
    landArea: Feature[AreaValue] | None = None
    mapPoint: Feature[MapPointValue] | None = None
    region: Feature[OptionValue] | None = None
    city: Feature[OptionValue] | None = None
    district: Feature[OptionValue] | None = None
    street: Feature[str] | None = None
    gas: Feature[bool] | None = None
    electricity: Feature[bool] | None = None
    water: Feature[bool] | None = None
    sewerage: Feature[bool] | None = None
    asphaltRoad: Feature[bool] | None = None
    inSettlement: Feature[bool] | None = None


class _SearchAds(_Raw):
    ads: list[dict[str, Any]]
    count: int


class _Data(_Raw):
    searchAds: _SearchAds


class _Envelope(_Raw):
    data: _Data


def parse_search_response(payload: dict[str, Any]) -> Page:
    """Разбирает ответ SearchAds. Битое объявление попадает в parse_errors, а не валит страницу."""
    if payload.get("errors"):
        raise SourceSchemaError(f"GraphQL вернул ошибки: {str(payload['errors'])[:500]}")
    try:
        envelope = _Envelope.model_validate(payload)
    except ValidationError as exc:
        raise SourceSchemaError(f"неожиданная структура ответа SearchAds: {exc}") from exc

    listings: list[Listing] = []
    errors: list[str] = []
    for item in envelope.data.searchAds.ads:
        try:
            listings.append(to_listing(RawAd.model_validate(item)))
        except ValidationError as exc:
            errors.append(f"объявление {item.get('id', '?')}: {exc.error_count()} ошибок разбора")
    return Page(listings=listings, total_count=envelope.data.searchAds.count, parse_errors=errors)


def to_listing(raw: RawAd) -> Listing:
    price = _value(raw.price)
    has_price = price is not None and bool(price.value)
    area = _value(raw.landArea)
    point = _value(raw.mapPoint)
    author = _value(raw.author)
    return Listing(
        id=raw.id,
        source=SOURCE_NAME,
        url=AD_URL.format(id=raw.id),
        title=raw.title,
        author_type=AUTHOR_TYPES.get(author.value, AUTHOR_UNKNOWN) if author else AUTHOR_UNKNOWN,
        region=_option(raw.region),
        settlement=_option(raw.city),
        zone=_option(raw.district),
        street=_clean_text(_value(raw.street)),
        lat=point.lat if point else None,
        lon=point.lon if point else None,
        area_sot=area.value if area is not None and area.unit == "UNIT_ARE" else None,
        land_type=_option(raw.landType),
        price_original=price.value if has_price else None,
        currency=price.unit.removeprefix("UNIT_") if has_price and price.unit else None,
        has_gas=_value(raw.gas),
        has_electricity=_value(raw.electricity),
        has_water=_value(raw.water),
        has_sewerage=_value(raw.sewerage),
        has_asphalt_road=_value(raw.asphaltRoad),
        in_settlement=_value(raw.inSettlement),
        description=_description(_value(raw.body)),
        images=tuple(IMAGE_URL.format(name=name) for name in (_value(raw.images) or [])),
        site_updated_at=raw.reseted,
    )


def scrub_phones(text: str) -> str:
    """Заменяет похожие на телефон последовательности (8–13 цифр) на заглушку."""

    def replace(match: re.Match[str]) -> str:
        digits = sum(ch.isdigit() for ch in match.group())
        return PHONE_PLACEHOLDER if 8 <= digits <= 13 else match.group()

    return _PHONE_CANDIDATE.sub(replace, text)


def _value(feature):
    return feature.value if feature is not None else None


def _option(feature: Feature[OptionValue] | None) -> str | None:
    option = _value(feature)
    return option.translated if option else None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value in {"", "-"} else value


def _description(body: BodyValue | None) -> str | None:
    if body is None:
        return None
    text = _clean_text(body.translated or body.ru or body.ro)
    return scrub_phones(text) if text else None
```

- [ ] **Step 7: Убедиться, что тесты проходят**

Run: `uv run pytest tests/nine99/test_query.py tests/nine99/test_parse.py -v`
Expected: 19 passed.

- [ ] **Step 8: Commit**

```bash
git add src/homealize/sources/nine99/query.py src/homealize/sources/nine99/parse.py tests/fixtures tests/nine99/test_query.py tests/nine99/test_parse.py
git commit -m "feat: 999.md search query and response parser"
```

---

### Task 5: Постраничный обход поиска 999.md

**Files:**
- Create: `src/homealize/sources/nine99/source.py`
- Test: `tests/nine99/test_source.py`

**Interfaces:**
- Consumes: `Settings` (Task 1); `PoliteClient`, `Page`, `EmptySearchError` (Task 3); `build_search_body`, `parse_search_response` (Task 4); `load_fixture` (Task 2).
- Produces:
  - `homealize.sources.nine99.source.Nine99Source(client: PoliteClient, *, page_size: int, area_min: float, area_max: float)`; атрибут `name = "999"`; методы `iter_pages() -> Iterator[Page]`, `close() -> None`. Реализует протокол `Source`.
  - `homealize.sources.nine99.source.build_nine99_source(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> Nine99Source`.

- [ ] **Step 1: Написать падающие тесты**

`tests/nine99/test_source.py`:

```python
import json

import httpx
import pytest

from homealize.config import Settings
from homealize.sources.base import EmptySearchError
from homealize.sources.nine99.http import PoliteClient
from homealize.sources.nine99.source import Nine99Source, build_nine99_source
from tests.factories import load_fixture

PAGES_BY_SKIP = {0: "search_page_1.json", 4: "search_page_2.json"}


def fixture_handler(bodies):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        skip = body["variables"]["input"]["pagination"]["skip"]
        return httpx.Response(200, json=load_fixture(PAGES_BY_SKIP[skip]))

    return handler


def make_source(handler, page_size=4) -> Nine99Source:
    client = PoliteClient(
        "https://999.md",
        headers={"lang": "ru"},
        delay_seconds=(0.0, 0.0),
        timeout_seconds=5,
        backoff_seconds=[0.0, 0.0, 0.0],
        max_retries=3,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    return Nine99Source(client, page_size=page_size, area_min=5, area_max=10)


def test_iterates_until_total_count():
    bodies = []
    pages = list(make_source(fixture_handler(bodies)).iter_pages())
    assert [len(page.listings) for page in pages] == [4, 1]
    assert len(pages[1].parse_errors) == 1
    assert [b["variables"]["input"]["pagination"] for b in bodies] == [
        {"limit": 4, "skip": 0},
        {"limit": 4, "skip": 4},
    ]


def test_zero_results_raise_empty_search():
    source = make_source(lambda request: httpx.Response(200, json={"data": {"searchAds": {"ads": [], "count": 0}}}))
    with pytest.raises(EmptySearchError):
        list(source.iter_pages())


def test_stops_on_empty_page_even_if_count_is_higher():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"searchAds": {"ads": [], "count": 500}}})

    pages = list(make_source(handler).iter_pages())
    assert len(pages) == 1
    assert len(requests) == 1


def test_build_from_settings_sends_russian_headers():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=load_fixture("search_page_2.json"))

    source = build_nine99_source(Settings(), transport=httpx.MockTransport(handler))
    try:
        pages = list(source.iter_pages())
    finally:
        source.close()
    assert len(pages) == 1
    request = requests[0]
    assert request.url == "https://999.md/graphql"
    assert request.headers["lang"] == "ru"
    assert request.headers["source"] == "desktop_redesign"
    assert request.headers["user-agent"].startswith("Mozilla/5.0")
    body = json.loads(request.content)
    assert body["variables"]["input"]["pagination"] == {"limit": 78, "skip": 0}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/nine99/test_source.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'homealize.sources.nine99.source'`.

- [ ] **Step 3: Реализовать источник**

`src/homealize/sources/nine99/source.py`:

```python
"""Источник объявлений 999.md: постраничный обход поиска SearchAds."""

import logging
from collections.abc import Iterator

import httpx

from homealize.config import Settings
from homealize.sources.base import EmptySearchError, Page
from homealize.sources.nine99.http import PoliteClient
from homealize.sources.nine99.parse import parse_search_response
from homealize.sources.nine99.query import build_search_body

log = logging.getLogger(__name__)

GRAPHQL_PATH = "/graphql"


class Nine99Source:
    name = "999"

    def __init__(self, client: PoliteClient, *, page_size: int, area_min: float, area_max: float) -> None:
        self._client = client
        self._page_size = page_size
        self._area_min = area_min
        self._area_max = area_max

    def iter_pages(self) -> Iterator[Page]:
        """Отдаёт страницы поиска по порядку, пока не пройдёт всё найденное."""
        skip = 0
        while True:
            body = build_search_body(
                skip=skip, limit=self._page_size, area_min=self._area_min, area_max=self._area_max
            )
            page = parse_search_response(self._client.post_json(GRAPHQL_PATH, body))
            if skip == 0 and page.total_count == 0:
                raise EmptySearchError("SearchAds вернул 0 объявлений")
            log.info(
                "999.md: страница с %s, объявлений %s из %s", skip, len(page.listings), page.total_count
            )
            yield page
            skip += self._page_size
            if skip >= page.total_count or not (page.listings or page.parse_errors):
                return

    def close(self) -> None:
        self._client.close()


def build_nine99_source(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> Nine99Source:
    """Источник 999.md с настройками из settings.yaml."""
    src = settings.source
    client = PoliteClient(
        src.base_url,
        headers={
            "User-Agent": src.user_agent,
            "Accept": "*/*",
            "Accept-Language": "ru",
            "lang": "ru",
            "source": "desktop_redesign",
        },
        delay_seconds=src.delay_seconds,
        timeout_seconds=src.timeout_seconds,
        backoff_seconds=src.backoff_seconds,
        max_retries=src.max_retries,
        transport=transport,
    )
    return Nine99Source(
        client,
        page_size=src.page_size,
        area_min=settings.filters.area_sot_min,
        area_max=settings.filters.area_sot_max,
    )
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/nine99 -v`
Expected: 31 passed (8 http + 3 query + 16 parse + 4 source).

- [ ] **Step 5: Commit**

```bash
git add src/homealize/sources/nine99/source.py tests/nine99/test_source.py
git commit -m "feat: paginated 999.md source"
```

---

### Task 6: База SQLite и миграции

**Files:**
- Create: `src/homealize/storage/__init__.py`, `src/homealize/storage/db.py`, `src/homealize/storage/migrations/__init__.py`, `src/homealize/storage/migrations/001_initial.sql`, `tests/conftest.py`, `tests/storage/__init__.py`
- Test: `tests/storage/test_db.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `homealize.storage.db.connect(path: str | Path) -> sqlite3.Connection` (`row_factory = sqlite3.Row`, внешние ключи включены, для файла режим WAL; `":memory:"` — база в памяти).
  - `homealize.storage.db.migrate(conn: sqlite3.Connection) -> int` — применяет недостающие `migrations/NNN_*.sql`, возвращает версию схемы (`PRAGMA user_version`).
  - Таблицы `listings`, `price_history`, `runs` (схема ниже).
  - Фикстура pytest `db` в `tests/conftest.py`: мигрированная база в памяти.

- [ ] **Step 1: Создать пакеты и фикстуру**

`src/homealize/storage/__init__.py`:

```python
"""Хранилище SQLite."""
```

`src/homealize/storage/migrations/__init__.py`:

```python
"""SQL-миграции: файлы NNN_описание.sql применяются по возрастанию номера."""
```

`tests/storage/__init__.py`: пустой файл.

`tests/conftest.py`:

```python
import pytest

from homealize.storage.db import connect, migrate


@pytest.fixture
def db():
    """Мигрированная база SQLite в памяти."""
    conn = connect(":memory:")
    migrate(conn)
    yield conn
    conn.close()
```

- [ ] **Step 2: Написать падающие тесты**

`tests/storage/test_db.py`:

```python
import sqlite3

import pytest

from homealize.storage.db import connect, migrate


def tables(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_migrate_creates_schema_v1():
    conn = connect(":memory:")
    assert migrate(conn) == 1
    assert {"listings", "price_history", "runs"} <= tables(conn)


def test_migrate_twice_is_noop():
    conn = connect(":memory:")
    migrate(conn)
    assert migrate(conn) == 1


def test_file_database_uses_wal_and_creates_folder(tmp_path):
    path = tmp_path / "nested" / "homealize.db"
    conn = connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
    assert path.exists()


def test_listing_status_must_be_known(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO listings (id, source, url, title, author_type, first_seen_at, last_seen_at, status) "
            "VALUES ('x', '999', 'u', 't', 'private', 'now', 'now', 'bogus')"
        )


def test_price_history_requires_existing_listing(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO price_history (listing_id, observed_at) VALUES ('missing', 'now')")
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: FAIL, `ImportError while loading conftest` … `No module named 'homealize.storage.db'` (фикстура `db` в `tests/conftest.py` уже ссылается на модуль, которого ещё нет).

- [ ] **Step 4: Написать миграцию**

`src/homealize/storage/migrations/001_initial.sql`:

```sql
CREATE TABLE listings (
    id                TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    url               TEXT NOT NULL,
    title             TEXT NOT NULL,
    region            TEXT,
    settlement        TEXT,
    zone              TEXT,
    street            TEXT,
    lat               REAL,
    lon               REAL,
    area_sot          REAL,
    land_type         TEXT,
    price_original    REAL,
    currency          TEXT,
    price_eur         REAL,
    price_per_sot_eur REAL,
    author_type       TEXT NOT NULL,
    has_gas           INTEGER,
    has_electricity   INTEGER,
    has_water         INTEGER,
    has_sewerage      INTEGER,
    has_asphalt_road  INTEGER,
    in_settlement     INTEGER,
    description       TEXT,
    images            TEXT NOT NULL DEFAULT '[]',
    site_updated_at   TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    missed_scans      INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL CHECK (status IN ('active', 'removed', 'filtered_out')),
    filter_reason     TEXT
);

CREATE INDEX idx_listings_status ON listings (status);

CREATE TABLE price_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id     TEXT NOT NULL REFERENCES listings (id),
    price_original REAL,
    currency       TEXT,
    price_eur      REAL,
    observed_at    TEXT NOT NULL
);

CREATE INDEX idx_price_history_listing ON price_history (listing_id, observed_at);

CREATE TABLE runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    search_complete INTEGER NOT NULL DEFAULT 0,
    ads_seen        INTEGER NOT NULL DEFAULT 0,
    new             INTEGER NOT NULL DEFAULT 0,
    changed         INTEGER NOT NULL DEFAULT 0,
    price_changed   INTEGER NOT NULL DEFAULT 0,
    removed         INTEGER NOT NULL DEFAULT 0,
    errors          TEXT NOT NULL DEFAULT '[]'
);
```

- [ ] **Step 5: Реализовать подключение и миграции**

`src/homealize/storage/db.py`:

```python
"""Подключение к SQLite и применение миграций."""

import sqlite3
from importlib import resources
from pathlib import Path

MIGRATIONS_PACKAGE = "homealize.storage.migrations"
IN_MEMORY = ":memory:"


def connect(path: str | Path) -> sqlite3.Connection:
    """Открывает базу; для файла создаёт папку и включает WAL, чтобы дашборд мог читать во время прогона."""
    in_memory = str(path) == IN_MEMORY
    if not in_memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Применяет недостающие миграции по порядку, каждую атомарно. Возвращает версию схемы."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in _migrations():
        if version <= current:
            continue
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        current = version
    return current


def _migrations() -> list[tuple[int, str]]:
    found = []
    for entry in resources.files(MIGRATIONS_PACKAGE).iterdir():
        if entry.name.endswith(".sql"):
            version = int(entry.name.split("_", 1)[0])
            found.append((version, entry.read_text(encoding="utf-8")))
    return sorted(found)
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add src/homealize/storage tests/conftest.py tests/storage/__init__.py tests/storage/test_db.py
git commit -m "feat: SQLite storage with numbered migrations"
```

---

### Task 7: Репозиторий: объявления, история цен, снятые, журнал прогонов

**Files:**
- Create: `src/homealize/storage/repo.py`
- Test: `tests/storage/test_repo.py`

**Interfaces:**
- Consumes: `Listing`, `RunSummary` (Task 2); фикстура `db`, схема (Task 6); `make_listing` (Task 2).
- Produces (все функции принимают `conn: sqlite3.Connection` первым аргументом и **не делают commit**: транзакциями управляет вызывающий):
  - `UpsertOutcome` (StrEnum): `NEW`, `PRICE_CHANGED`, `UPDATED`, `UNCHANGED`.
  - `upsert_listing(conn, listing: Listing, *, status: str, filter_reason: str | None, now: datetime) -> UpsertOutcome`.
  - `mark_unseen(conn, seen_ids: set[str], *, threshold: int) -> int` — число объявлений, снятых в этот раз.
  - `start_run(conn, now: datetime) -> int`; `finish_run(conn, run_id: int, summary: RunSummary, *, now: datetime) -> None`.
  - `get_listing(conn, listing_id: str) -> sqlite3.Row | None`; `get_price_history(conn, listing_id: str) -> list[sqlite3.Row]`; `last_run(conn) -> sqlite3.Row | None`.

- [ ] **Step 1: Написать падающие тесты**

`tests/storage/test_repo.py`:

```python
import json
from datetime import UTC, datetime, timedelta

from homealize.models import RunSummary
from homealize.storage import repo
from homealize.storage.repo import UpsertOutcome
from tests.factories import make_listing

T0 = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=3)


def save(db, listing, *, now=T0, status="active", reason=None):
    outcome = repo.upsert_listing(db, listing, status=status, filter_reason=reason, now=now)
    db.commit()
    return outcome


def test_new_listing_is_inserted_with_first_price(db):
    assert save(db, make_listing()) is UpsertOutcome.NEW
    row = repo.get_listing(db, "100000001")
    assert row["status"] == "active"
    assert row["first_seen_at"] == row["last_seen_at"] == T0.isoformat()
    assert row["missed_scans"] == 0
    assert row["price_eur"] == 28000.0
    assert row["price_per_sot_eur"] == 4666.67
    assert (row["has_gas"], row["has_sewerage"]) == (1, 0)
    assert json.loads(row["images"]) == list(make_listing().images)
    history = repo.get_price_history(db, "100000001")
    assert [(h["price_original"], h["currency"]) for h in history] == [(28000.0, "EUR")]


def test_same_listing_again_is_unchanged(db):
    save(db, make_listing())
    assert save(db, make_listing(), now=T1) is UpsertOutcome.UNCHANGED
    row = repo.get_listing(db, "100000001")
    assert row["first_seen_at"] == T0.isoformat()
    assert row["last_seen_at"] == T1.isoformat()
    assert len(repo.get_price_history(db, "100000001")) == 1


def test_price_change_is_recorded(db):
    save(db, make_listing())
    assert save(db, make_listing(price_original=26000.0), now=T1) is UpsertOutcome.PRICE_CHANGED
    assert [h["price_eur"] for h in repo.get_price_history(db, "100000001")] == [28000.0, 26000.0]
    assert repo.get_listing(db, "100000001")["price_eur"] == 26000.0


def test_other_change_is_update_without_price_history(db):
    save(db, make_listing())
    assert save(db, make_listing(has_gas=False), now=T1) is UpsertOutcome.UPDATED
    assert repo.get_listing(db, "100000001")["has_gas"] == 0
    assert len(repo.get_price_history(db, "100000001")) == 1


def test_price_disappearing_is_update_not_price_change(db):
    save(db, make_listing())
    assert save(db, make_listing(price_original=None, currency=None), now=T1) is UpsertOutcome.UPDATED
    assert len(repo.get_price_history(db, "100000001")) == 1


def test_filter_reason_is_saved(db):
    save(db, make_listing(region="Кагул"), status="filtered_out", reason="region")
    row = repo.get_listing(db, "100000001")
    assert (row["status"], row["filter_reason"]) == ("filtered_out", "region")


def test_unseen_listing_removed_after_threshold(db):
    save(db, make_listing(id="a"))
    save(db, make_listing(id="b"))
    assert repo.mark_unseen(db, {"a"}, threshold=2) == 0
    assert (repo.get_listing(db, "b")["missed_scans"], repo.get_listing(db, "b")["status"]) == (1, "active")
    assert repo.mark_unseen(db, {"a"}, threshold=2) == 1
    assert repo.get_listing(db, "b")["status"] == "removed"
    assert repo.get_listing(db, "a")["missed_scans"] == 0


def test_removed_listing_returns_to_active_when_seen_again(db):
    save(db, make_listing(id="b"))
    repo.mark_unseen(db, set(), threshold=1)
    assert repo.get_listing(db, "b")["status"] == "removed"
    save(db, make_listing(id="b"), now=T1)
    row = repo.get_listing(db, "b")
    assert (row["status"], row["missed_scans"]) == ("active", 0)


def test_run_lifecycle(db):
    run_id = repo.start_run(db, T0)
    assert repo.last_run(db)["status"] == "running"
    summary = RunSummary(
        search_complete=True, ads_seen=5, new=2, changed=1, price_changed=1, removed=0,
        errors=["объявление x: 1 ошибок разбора"],
    )
    repo.finish_run(db, run_id, summary, now=T1)
    row = repo.last_run(db)
    assert row["status"] == "ok"
    assert row["finished_at"] == T1.isoformat()
    assert (row["ads_seen"], row["new"], row["changed"], row["price_changed"], row["removed"]) == (5, 2, 1, 1, 0)
    assert json.loads(row["errors"]) == ["объявление x: 1 ошибок разбора"]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/storage/test_repo.py -v`
Expected: FAIL, `ImportError: cannot import name 'repo' from 'homealize.storage'`.

- [ ] **Step 3: Реализовать репозиторий**

`src/homealize/storage/repo.py`:

```python
"""Чтение и запись объявлений, истории цен и журнала прогонов.

Функции не делают commit: транзакциями управляет вызывающий (pipeline).
"""

import json
import sqlite3
from datetime import datetime
from enum import StrEnum

from homealize.models import Listing, RunSummary


class UpsertOutcome(StrEnum):
    NEW = "new"
    PRICE_CHANGED = "price_changed"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


def upsert_listing(
    conn: sqlite3.Connection,
    listing: Listing,
    *,
    status: str,
    filter_reason: str | None,
    now: datetime,
) -> UpsertOutcome:
    """Сохраняет объявление; при смене цены пишет price_history. Сбрасывает счётчик пропусков."""
    values = _content(listing)
    ts = now.isoformat()
    existing = get_listing(conn, listing.id)
    if existing is None:
        columns = ["id", *values, "first_seen_at", "last_seen_at", "missed_scans", "status", "filter_reason"]
        params = [listing.id, *values.values(), ts, ts, 0, status, filter_reason]
        conn.execute(
            f"INSERT INTO listings ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            params,
        )
        _record_price(conn, listing, ts)
        return UpsertOutcome.NEW

    price_changed = listing.price_original is not None and (
        (existing["price_original"], existing["currency"]) != (listing.price_original, listing.currency)
    )
    content_changed = any(existing[column] != value for column, value in values.items())
    assignments = ", ".join(f"{column} = ?" for column in values)
    conn.execute(
        f"UPDATE listings SET {assignments}, last_seen_at = ?, missed_scans = 0, status = ?, filter_reason = ? "
        "WHERE id = ?",
        [*values.values(), ts, status, filter_reason, listing.id],
    )
    if price_changed:
        _record_price(conn, listing, ts)
        return UpsertOutcome.PRICE_CHANGED
    return UpsertOutcome.UPDATED if content_changed else UpsertOutcome.UNCHANGED


def mark_unseen(conn: sqlite3.Connection, seen_ids: set[str], *, threshold: int) -> int:
    """После полного прохода: +1 пропуск всем, кого не видели; снимает тех, кто пропущен threshold раз."""
    rows = conn.execute("SELECT id, missed_scans FROM listings WHERE status != 'removed'").fetchall()
    removed = 0
    for row in rows:
        if row["id"] in seen_ids:
            continue
        missed = row["missed_scans"] + 1
        if missed >= threshold:
            conn.execute("UPDATE listings SET missed_scans = ?, status = 'removed' WHERE id = ?", (missed, row["id"]))
            removed += 1
        else:
            conn.execute("UPDATE listings SET missed_scans = ? WHERE id = ?", (missed, row["id"]))
    return removed


def start_run(conn: sqlite3.Connection, now: datetime) -> int:
    cursor = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now.isoformat(),))
    return cursor.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, summary: RunSummary, *, now: datetime) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, search_complete = ?, ads_seen = ?, new = ?, changed = ?, "
        "price_changed = ?, removed = ?, errors = ? WHERE id = ?",
        (
            now.isoformat(),
            summary.status,
            summary.search_complete,
            summary.ads_seen,
            summary.new,
            summary.changed,
            summary.price_changed,
            summary.removed,
            json.dumps(summary.errors, ensure_ascii=False),
            run_id,
        ),
    )


def get_listing(conn: sqlite3.Connection, listing_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()


def get_price_history(conn: sqlite3.Connection, listing_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM price_history WHERE listing_id = ? ORDER BY observed_at, id", (listing_id,)
    ).fetchall()


def last_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()


def _content(listing: Listing) -> dict[str, object]:
    """Колонки listings, значения которых берутся из самого объявления."""
    return {
        "source": listing.source,
        "url": listing.url,
        "title": listing.title,
        "region": listing.region,
        "settlement": listing.settlement,
        "zone": listing.zone,
        "street": listing.street,
        "lat": listing.lat,
        "lon": listing.lon,
        "area_sot": listing.area_sot,
        "land_type": listing.land_type,
        "price_original": listing.price_original,
        "currency": listing.currency,
        "price_eur": listing.price_eur,
        "price_per_sot_eur": listing.price_per_sot_eur,
        "author_type": listing.author_type,
        "has_gas": listing.has_gas,
        "has_electricity": listing.has_electricity,
        "has_water": listing.has_water,
        "has_sewerage": listing.has_sewerage,
        "has_asphalt_road": listing.has_asphalt_road,
        "in_settlement": listing.in_settlement,
        "description": listing.description,
        "images": json.dumps(list(listing.images), ensure_ascii=False),
        "site_updated_at": listing.site_updated_at.isoformat() if listing.site_updated_at else None,
    }


def _record_price(conn: sqlite3.Connection, listing: Listing, ts: str) -> None:
    if listing.price_original is None:
        return
    conn.execute(
        "INSERT INTO price_history (listing_id, price_original, currency, price_eur, observed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (listing.id, listing.price_original, listing.currency, listing.price_eur, ts),
    )
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/storage -v`
Expected: 14 passed (5 db + 9 repo).

- [ ] **Step 5: Commit**

```bash
git add src/homealize/storage/repo.py tests/storage/test_repo.py
git commit -m "feat: listing repository with price history and removal tracking"
```

---

### Task 8: Прогон `run_once`

**Files:**
- Create: `src/homealize/pipeline.py`
- Modify: `tests/factories.py` (заменить целиком, добавляются `FakeSource` и `page`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Settings` (Task 1); `RunSummary`, `filter_reason`, `load_fixture` (Task 2); `Source`, `SourceError`, `SourceUnavailableError`, `Page` (Task 3); фикстуры `search_page_*.json` (Task 4); `build_nine99_source` (Task 5); фикстура `db` (Task 6); `repo.*`, `UpsertOutcome` (Task 7).
- Produces:
  - `homealize.pipeline.run_once(conn: sqlite3.Connection, source: Source, settings: Settings, *, now: datetime) -> RunSummary`.
  - `tests.factories.FakeSource(pages: list[Page], *, fail_at: int | None = None, error: Exception | None = None)` с `name = "fake"`, `iter_pages()`, `close()` и флагом `closed`; `tests.factories.page(*listings, errors=()) -> Page`.

- [ ] **Step 1: Дополнить помощники для тестов**

Заменить `tests/factories.py` целиком:

```python
"""Помощники для тестов: готовые объявления, фикстуры 999.md, поддельный источник."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homealize.models import Listing
from homealize.sources.base import Page, SourceUnavailableError

FIXTURES = Path(__file__).parent / "fixtures"
CHISINAU_TZ = timezone(timedelta(hours=2))


def make_listing(**overrides) -> Listing:
    """Корректное объявление, проходящее все фильтры; поля можно переопределить."""
    fields = {
        "id": "100000001",
        "source": "999",
        "url": "https://999.md/ru/100000001",
        "title": "6 сот., участок под строительство",
        "author_type": "private",
        "region": "Кишинёв мун.",
        "settlement": "Гратиешты",
        "zone": "Окраина",
        "street": None,
        "lat": 47.09921,
        "lon": 28.8165,
        "area_sot": 6.0,
        "land_type": "Участок под строительство",
        "price_original": 28000.0,
        "currency": "EUR",
        "has_gas": True,
        "has_electricity": True,
        "has_water": True,
        "has_sewerage": False,
        "has_asphalt_road": False,
        "in_settlement": True,
        "description": "Ровный участок.",
        "images": ("https://i.simpalsmedia.com/999.md/BoardImages/900x900/aaa111.jpg",),
        "site_updated_at": datetime(2026, 10, 28, 23, 15, tzinfo=CHISINAU_TZ),
    }
    fields.update(overrides)
    return Listing(**fields)


def load_fixture(name: str) -> dict:
    """Читает JSON-фикстуру ответа 999.md из tests/fixtures/nine99/."""
    return json.loads((FIXTURES / "nine99" / name).read_text(encoding="utf-8"))


def page(*listings: Listing, errors: tuple[str, ...] = ()) -> Page:
    return Page(listings=list(listings), total_count=len(listings), parse_errors=list(errors))


class FakeSource:
    """Источник для тестов: отдаёт заданные страницы; с fail_at падает перед страницей с этим номером."""

    name = "fake"

    def __init__(self, pages: list[Page], *, fail_at: int | None = None, error: Exception | None = None):
        self._pages = pages
        self._fail_at = fail_at
        self._error = error or SourceUnavailableError("сеть недоступна")
        self.closed = False

    def iter_pages(self):
        for index, item in enumerate(self._pages):
            if index == self._fail_at:
                raise self._error
            yield item
        if self._fail_at == len(self._pages):
            raise self._error

    def close(self) -> None:
        self.closed = True
```

- [ ] **Step 2: Написать падающие тесты**

`tests/test_pipeline.py`:

```python
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from homealize.config import Settings
from homealize.pipeline import run_once
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage import repo
from tests.factories import FakeSource, load_fixture, make_listing, page

T0 = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
SETTINGS = Settings()


def at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def test_first_run_saves_active_and_filtered(db):
    source = FakeSource([page(make_listing(id="a"), make_listing(id="b", region="Кагул"))])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert (summary.search_complete, summary.ads_seen, summary.new) == (True, 2, 2)
    assert repo.get_listing(db, "a")["status"] == "active"
    b = repo.get_listing(db, "b")
    assert (b["status"], b["filter_reason"]) == ("filtered_out", "region")
    assert repo.last_run(db)["status"] == "ok"


def test_price_drop_detected_on_next_run(db):
    run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=T0)
    summary = run_once(db, FakeSource([page(make_listing(id="a", price_original=25000.0))]), SETTINGS, now=at(3))
    assert (summary.new, summary.price_changed) == (0, 1)
    assert [h["price_eur"] for h in repo.get_price_history(db, "a")] == [28000.0, 25000.0]


def test_listing_removed_after_two_complete_runs_without_it(db):
    run_once(db, FakeSource([page(make_listing(id="a"), make_listing(id="b"))]), SETTINGS, now=T0)
    second = run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=at(3))
    assert second.removed == 0
    third = run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=at(6))
    assert third.removed == 1
    assert repo.get_listing(db, "b")["status"] == "removed"


def test_interrupted_scan_keeps_saved_pages_and_removes_nothing(db):
    run_once(db, FakeSource([page(make_listing(id="a"), make_listing(id="b"))]), SETTINGS, now=T0)
    broken = FakeSource([page(make_listing(id="a")), page(make_listing(id="c"))], fail_at=1)
    summary = run_once(db, broken, SETTINGS, now=at(3))
    assert summary.search_complete is False
    assert any("SourceUnavailableError" in error for error in summary.errors)
    assert repo.get_listing(db, "a")["last_seen_at"] == at(3).isoformat()
    assert repo.get_listing(db, "b")["missed_scans"] == 0
    assert repo.get_listing(db, "c") is None
    assert repo.last_run(db)["status"] == "failed"


def test_duplicates_across_pages_counted_once(db):
    source = FakeSource([
        page(make_listing(id="a"), make_listing(id="b")),
        page(make_listing(id="b"), make_listing(id="c")),
    ])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert (summary.ads_seen, summary.new) == (3, 3)


def test_parse_errors_are_saved_in_run(db):
    source = FakeSource([page(make_listing(id="a"), errors=("объявление x: 2 ошибок разбора",))])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert summary.errors == ["объявление x: 2 ошибок разбора"]
    assert json.loads(repo.last_run(db)["errors"]) == summary.errors


def test_unexpected_error_marks_run_failed_and_propagates(db):
    source = FakeSource([page(make_listing(id="a"))], fail_at=0, error=RuntimeError("баг"))
    with pytest.raises(RuntimeError):
        run_once(db, source, SETTINGS, now=T0)
    run = repo.last_run(db)
    assert run["status"] == "failed"
    assert json.loads(run["errors"]) == ["RuntimeError: баг"]


def test_end_to_end_with_999_fixtures_over_http(db):
    pages_by_skip = {0: "search_page_1.json", 4: "search_page_2.json"}

    def handler(request: httpx.Request) -> httpx.Response:
        skip = json.loads(request.content)["variables"]["input"]["pagination"]["skip"]
        return httpx.Response(200, json=load_fixture(pages_by_skip[skip]))

    settings = Settings.model_validate({"source": {"page_size": 4, "delay_seconds": [0, 0]}})
    source = build_nine99_source(settings, transport=httpx.MockTransport(handler))
    try:
        summary = run_once(db, source, settings, now=T0)
    finally:
        source.close()
    assert summary.search_complete
    assert (summary.ads_seen, summary.new) == (5, 5)
    assert len(summary.errors) == 1
    statuses = {
        row["id"]: (row["status"], row["filter_reason"])
        for row in db.execute("SELECT id, status, filter_reason FROM listings")
    }
    assert statuses == {
        "100000001": ("active", None),
        "100000002": ("active", None),
        "100000003": ("filtered_out", "region"),
        "100000004": ("filtered_out", "area"),
        "100000006": ("filtered_out", "no_price"),
    }
    assert "[телефон удалён]" in repo.get_listing(db, "100000001")["description"]
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'homealize.pipeline'`.

- [ ] **Step 4: Реализовать прогон**

`src/homealize/pipeline.py`:

```python
"""Один прогон: обход источника, фильтры, сохранение, история цен, снятие пропавших."""

import logging
import sqlite3
from datetime import datetime

from homealize.config import Settings
from homealize.filters import filter_reason
from homealize.models import RunSummary
from homealize.sources.base import Source, SourceError
from homealize.storage import repo
from homealize.storage.repo import UpsertOutcome

log = logging.getLogger(__name__)


def run_once(conn: sqlite3.Connection, source: Source, settings: Settings, *, now: datetime) -> RunSummary:
    summary = RunSummary()
    run_id = repo.start_run(conn, now)
    conn.commit()
    seen: set[str] = set()
    try:
        for page in source.iter_pages():
            summary.errors.extend(page.parse_errors)
            for listing in page.listings:
                if listing.id in seen:
                    continue
                seen.add(listing.id)
                reason = filter_reason(listing, settings.filters)
                outcome = repo.upsert_listing(
                    conn,
                    listing,
                    status="filtered_out" if reason else "active",
                    filter_reason=reason,
                    now=now,
                )
                _count(summary, outcome)
            conn.commit()
        summary.search_complete = True
    except SourceError as exc:
        conn.rollback()
        log.error("Обход %s прерван: %s", source.name, exc)
        summary.errors.append(f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        conn.rollback()
        summary.errors.append(f"{type(exc).__name__}: {exc}")
        _finish(conn, run_id, summary, seen, now)
        raise

    if summary.search_complete:
        summary.removed = repo.mark_unseen(conn, seen, threshold=settings.lifecycle.removal_missed_scans)
    _finish(conn, run_id, summary, seen, now)
    log.info(
        "Прогон %s завершён (%s): увидено %s, новых %s, цена изменилась %s, обновлено %s, снято %s, ошибок %s",
        run_id, summary.status, summary.ads_seen, summary.new, summary.price_changed,
        summary.changed, summary.removed, len(summary.errors),
    )
    return summary


def _count(summary: RunSummary, outcome: UpsertOutcome) -> None:
    if outcome is UpsertOutcome.NEW:
        summary.new += 1
    elif outcome is UpsertOutcome.PRICE_CHANGED:
        summary.price_changed += 1
    elif outcome is UpsertOutcome.UPDATED:
        summary.changed += 1


def _finish(conn: sqlite3.Connection, run_id: int, summary: RunSummary, seen: set[str], now: datetime) -> None:
    summary.ads_seen = len(seen)
    repo.finish_run(conn, run_id, summary, now=now)
    conn.commit()
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `uv run pytest -v`
Expected: все офлайн-тесты проходят (7 config + 17 models/filters + 31 nine99 + 14 storage + 8 pipeline = 77 passed).

- [ ] **Step 6: Commit**

```bash
git add src/homealize/pipeline.py tests/factories.py tests/test_pipeline.py
git commit -m "feat: run_once pipeline"
```

---

### Task 9: CLI, логи, смоук-тест и первый настоящий прогон

**Files:**
- Create: `src/homealize/logging_setup.py`, `src/homealize/cli.py`, `tests/live/__init__.py`, `tests/live/test_live_999.py`, `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_settings`, `LoggingSettings` (Task 1); `build_nine99_source` (Task 5); `connect`, `migrate` (Task 6); `run_once` (Task 8); `FakeSource`, `page`, `make_listing` (Task 8).
- Produces:
  - `homealize.logging_setup.configure_logging(cfg: LoggingSettings) -> None`, `reset_logging() -> None`.
  - `homealize.cli.main(argv: list[str] | None = None) -> int`: команды `init-db`, `run`, общий ключ `--config` (по умолчанию `config/settings.yaml`). `run` возвращает 0 при полном проходе, 1 при прерванном.
  - Консольная команда `homealize` (через `[project.scripts]` из Task 1).

- [ ] **Step 1: Написать падающие тесты CLI**

`tests/test_cli.py`:

```python
from pathlib import Path

import pytest

from homealize import cli
from homealize.logging_setup import reset_logging
from tests.factories import FakeSource, make_listing, page


@pytest.fixture(autouse=True)
def _clean_logging():
    yield
    reset_logging()


def write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"storage:\n  db_path: '{(tmp_path / 'data' / 'test.db').as_posix()}'\n"
        f"logging:\n  path: '{(tmp_path / 'logs' / 'test.log').as_posix()}'\n",
        encoding="utf-8",
    )
    return cfg


def test_init_db_creates_database(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "init-db"]) == 0
    assert (tmp_path / "data" / "test.db").exists()
    assert "схема v1" in capsys.readouterr().out


def test_run_saves_listings_and_prints_summary(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    fake = FakeSource([page(make_listing(id="a"), make_listing(id="b", region="Кагул"))])
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: fake)
    assert cli.main(["--config", str(cfg), "run"]) == 0
    out = capsys.readouterr().out
    assert "увидено 2" in out
    assert "новых 2" in out
    assert fake.closed
    reset_logging()
    assert "Прогон" in (tmp_path / "logs" / "test.log").read_text(encoding="utf-8")


def test_run_returns_1_when_scan_interrupted(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([], fail_at=0))
    assert cli.main(["--config", str(cfg), "run"]) == 1
    assert "НЕ завершён" in capsys.readouterr().out
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL, `ImportError: cannot import name 'cli' from 'homealize'`.

- [ ] **Step 3: Реализовать логи**

`src/homealize/logging_setup.py`:

```python
"""Логи: файл с ротацией и консоль."""

import logging
from logging.handlers import RotatingFileHandler

from homealize.config import LoggingSettings

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_homealize_handler"


def configure_logging(cfg: LoggingSettings) -> None:
    reset_logging()
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)
    file_handler = RotatingFileHandler(
        cfg.path, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count, encoding="utf-8"
    )
    console_handler = logging.StreamHandler()
    root = logging.getLogger()
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def reset_logging() -> None:
    """Снимает и закрывает обработчики, добавленные configure_logging()."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
            handler.close()
```

- [ ] **Step 4: Реализовать CLI**

`src/homealize/cli.py`:

```python
"""Командная строка: homealize init-db | run."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from homealize.config import Settings, load_settings
from homealize.logging_setup import configure_logging
from homealize.pipeline import run_once
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage.db import connect, migrate

DEFAULT_CONFIG = Path("config/settings.yaml")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="homealize", description="Мониторинг участков под строительство на 999.md")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="путь к settings.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="создать или обновить базу")
    commands.add_parser("run", help="один прогон: собрать объявления и сохранить")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    if args.command == "init-db":
        return _init_db(settings)
    return _run(settings)


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
        summary = run_once(conn, source, settings, now=datetime.now(UTC))
    finally:
        source.close()
        conn.close()
    state = "успешен" if summary.search_complete else "НЕ завершён"
    print(
        f"Прогон {state}: увидено {summary.ads_seen}, новых {summary.new}, "
        f"цена изменилась {summary.price_changed}, обновлено {summary.changed}, "
        f"снято {summary.removed}, ошибок {len(summary.errors)}"
    )
    return 0 if summary.search_complete else 1


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

- [ ] **Step 5: Убедиться, что тесты CLI проходят**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 6: Добавить смоук-тест против настоящего сайта**

`tests/live/__init__.py`: пустой файл.

`tests/live/test_live_999.py`:

```python
"""Проверка, что 999.md всё ещё отвечает в ожидаемом формате. Один запрос; запускать вручную."""

import pytest

from homealize.config import Settings
from homealize.sources.nine99.source import build_nine99_source

pytestmark = pytest.mark.live


def test_first_search_page_matches_expected_schema():
    settings = Settings()
    source = build_nine99_source(settings)
    try:
        first = next(source.iter_pages())
    finally:
        source.close()
    assert first.total_count > 0
    assert first.listings, "поиск не вернул ни одного объявления"
    assert first.parse_errors == []
    assert all(listing.land_type == settings.filters.land_type for listing in first.listings)
    assert all(listing.area_sot is not None for listing in first.listings)
    with_coords = sum(1 for listing in first.listings if listing.lat is not None)
    assert with_coords >= len(first.listings) // 2
```

- [ ] **Step 7: Написать README**

`README.md`:

````markdown
# Homealize

Мониторинг участков под строительство дома на 999.md. Этап 1: сбор объявлений, история цен, отметки «отфильтровано» и «снято с продажи».

Спецификация: `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md`.

## Быстрый старт

```bash
uv sync
uv run homealize init-db
uv run homealize run
```

- Настройки: `config/settings.yaml`.
- База: `data/homealize.db` (SQLite).
- Логи: `logs/homealize.log`.

Один прогон делает около 23 запросов к 999.md с паузами 2–3 с и занимает около минуты.

## Тесты

```bash
uv run pytest
uv run pytest -m live
```

Первая команда запускает офлайн-тесты. Вторая делает один настоящий запрос к 999.md и проверяет, что формат ответа не изменился.
````

- [ ] **Step 8: Прогнать все офлайн-тесты**

Run: `uv run pytest -v`
Expected: 80 passed, live-тест не запускается (1 deselected).

- [ ] **Step 9: Смоук-тест против настоящего сайта**

Run: `uv run pytest -m live -v`
Expected: 1 passed. Если упал на `parse_errors` или `SourceSchemaError`, сайт изменил формат: сравнить ответ с §3 спеки и обновить `query.py`/`parse.py` и фикстуры, прежде чем идти дальше.

- [ ] **Step 10: Первый настоящий прогон**

Run: `uv run homealize init-db`
Expected: `База готова: data/homealize.db (схема v1)`.

Run: `uv run homealize run`
Expected: около минуты, затем строка вида `Прогон успешен: увидено ~1700–1900, новых ~1700–1900, цена изменилась 0, обновлено 0, снято 0, ошибок 0` и код выхода 0.

Проверить содержимое базы:

Run: `uv run python -c "import sqlite3; c = sqlite3.connect('data/homealize.db'); print(c.execute('SELECT status, filter_reason, COUNT(*) FROM listings GROUP BY 1, 2').fetchall()); print(c.execute('SELECT COUNT(*) FROM price_history').fetchone())"`
Expected: большинство объявлений `('active', None, N)`, остальные `('filtered_out', 'region', M)`; строк в `price_history` примерно столько же, сколько объявлений с ценой.

Run: `uv run homealize run`
Expected: второй прогон: `новых` около 0, `снято 0` (объявление снимается только после двух полных проходов без него).

- [ ] **Step 11: Commit**

```bash
git add src/homealize/logging_setup.py src/homealize/cli.py tests/test_cli.py tests/live README.md
git commit -m "feat: CLI, logging and live smoke test"
```
