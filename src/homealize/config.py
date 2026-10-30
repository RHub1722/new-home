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
