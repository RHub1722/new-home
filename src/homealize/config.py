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
