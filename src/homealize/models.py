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
    region_id: int | None = None  # номер опции «Регион» на сайте
    settlement_id: int | None = None  # номер опции «Населённый пункт» на сайте
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


@dataclass(frozen=True)
class Poi:
    """Объект инфраструктуры из OpenStreetMap (контакты организаций не храним)."""

    key: str  # "node/123", "way/456"
    category: str  # одна из config.POI_CATEGORIES
    name: str | None
    brand: str | None
    lat: float
    lon: float
