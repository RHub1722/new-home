"""Настройки Homealize: читаются из config/settings.yaml и проверяются pydantic."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
DEFAULT_RUN_TIMES = ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"]
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
CHAT_VAR = "TELEGRAM_CHAT_ID"


def parse_hhmm(value: str) -> time:
    """«07:05» → time(7, 5); формат уже проверен ScheduleSettings."""
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _check_hhmm(field_name: str, value: str) -> None:
    if not _HHMM.match(value):
        raise ValueError(f"{field_name}: «{value}» — нужно время ЧЧ:ММ, например 07:00")


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
    run_lock_path: Path = Path("data/run.lock")  # пока идёт прогон, второй не начнётся
    serve_lock_path: Path = Path("data/serve.lock")  # второй serve не запустится


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


class ScheduleSettings(_Section):
    timezone: str = "Europe/Chisinau"
    run_times: list[str] = Field(default_factory=lambda: list(DEFAULT_RUN_TIMES))
    digest_time: str = "20:00"
    catch_up_gap_minutes: int = Field(60, ge=0)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"timezone: неизвестный часовой пояс «{value}»") from exc
        return value

    @field_validator("run_times")
    @classmethod
    def _check_run_times(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("run_times: нужно хотя бы одно время прогона")
        for value in values:
            _check_hhmm("run_times", value)
        if len(set(values)) != len(values):
            raise ValueError("run_times: время повторяется")
        return sorted(values)

    @field_validator("digest_time")
    @classmethod
    def _check_digest_time(cls, value: str) -> str:
        _check_hhmm("digest_time", value)
        return value

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class TelegramSettings(_Section):
    env_file: Path = Path(".env")  # TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
    api_url: str = "https://api.telegram.org"
    timeout_seconds: float = Field(30.0, gt=0)
    poll_timeout_seconds: int = Field(50, ge=1, le=50)
    send_pause_seconds: float = Field(1.0, ge=0)
    new_min_total: float = Field(70.0, ge=0, le=100)
    new_max_rank: int = Field(10, ge=0)
    max_new_per_run: int = Field(10, ge=1)
    price_drop_min_share: float = Field(0.05, gt=0, lt=1)
    price_drop_min_total: float = Field(60.0, ge=0, le=100)
    alert_repeat_hours: float = Field(6.0, ge=0)
    digest_top: int = Field(10, ge=1, le=30)
    top_default: int = Field(10, ge=1)
    top_max: int = Field(30, ge=1)
    max_attempts: int = Field(3, ge=1)
    expire_hours: float = Field(24.0, gt=0)

    @model_validator(mode="after")
    def _check_top(self) -> "TelegramSettings":
        if self.top_default > self.top_max:
            raise ValueError("top_default не может быть больше top_max")
        return self


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
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)


def load_settings(path: Path) -> Settings:
    """Читает YAML-файл настроек; отсутствующие ключи берутся по умолчанию."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)


@dataclass(frozen=True)
class TelegramSecrets:
    token: str = field(repr=False)
    chat_id: int | None  # None: бот создан, но чат ещё не известен (см. `homealize telegram-test`)


def read_env_file(path: Path) -> dict[str, str]:
    """Строки ИМЯ=значение; пустые строки и # комментарии пропускаются, кавычки вокруг значения снимаются."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"{path}, строка {number}: нужно ИМЯ=значение")  # само значение не показываем
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_telegram_secrets(path: Path, environ: Mapping[str, str] | None = None) -> TelegramSecrets | None:
    """Токен и чат из окружения или .env (окружение важнее); None — Telegram не настроен."""
    environ = os.environ if environ is None else environ
    file_values = read_env_file(path)
    token = (environ.get(TOKEN_VAR) or file_values.get(TOKEN_VAR) or "").strip()
    if not token:
        return None
    chat = (environ.get(CHAT_VAR) or file_values.get(CHAT_VAR) or "").strip()
    if not chat:
        return TelegramSecrets(token, None)
    try:
        return TelegramSecrets(token, int(chat))
    except ValueError:
        raise ValueError(f"{CHAT_VAR} должен быть числом, сейчас «{chat}»") from None
