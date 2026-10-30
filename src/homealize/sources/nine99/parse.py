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
# Разделители в телефонах: пробел, дефис, U+2010, en dash, em dash, косая черта, скобки, точка.
_PHONE_CANDIDATE = re.compile(r"[+(]?\d[\d\s\-\u2010\u2013\u2014/().]{6,}\d")

T = TypeVar("T")


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Feature(_Raw, Generic[T]):
    value: T | None = None


class OptionValue(_Raw):
    translated: str | None = None
    value: int | str | None = None


class PriceValue(_Raw):
    value: float | None = None
    unit: str | None = None
    mode: str | None = None


class AreaValue(_Raw):
    value: float
    unit: str


class MapPointValue(_Raw):
    lat: float | None = None
    lon: float | None = None


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
        problems = "; ".join(f"{_location(e)}: {e['msg']}" for e in exc.errors(include_input=False)[:3])
        raise SourceSchemaError(f"неожиданная структура ответа SearchAds: {problems}") from exc

    listings: list[Listing] = []
    errors: list[str] = []
    unparsed_ids: list[str] = []
    for item in envelope.data.searchAds.ads:
        try:
            listings.append(to_listing(RawAd.model_validate(item)))
        except ValidationError as exc:
            ad_id = str(item.get("id") or "")
            first = exc.errors(include_input=False)[0]
            errors.append(
                f"объявление {ad_id or '?'}: {_location(first)} — {first['msg']} (ошибок: {exc.error_count()})"
            )
            if ad_id:
                unparsed_ids.append(ad_id)
    return Page(
        listings=listings,
        total_count=envelope.data.searchAds.count,
        parse_errors=errors,
        unparsed_ids=unparsed_ids,
    )


def _location(error: Any) -> str:
    """Путь к полю из ошибки pydantic; значения входных данных в текст не попадают."""
    return ".".join(str(part) for part in error["loc"])


def to_listing(raw: RawAd) -> Listing:
    price = _value(raw.price)
    has_price = price is not None and bool(price.value)
    area = _value(raw.landArea)
    point = _value(raw.mapPoint)
    has_point = point is not None and point.lat is not None and point.lon is not None
    author = _value(raw.author)
    return Listing(
        id=raw.id,
        source=SOURCE_NAME,
        url=AD_URL.format(id=raw.id),
        title=scrub_phones(raw.title),
        author_type=AUTHOR_TYPES.get(author.value, AUTHOR_UNKNOWN) if author else AUTHOR_UNKNOWN,
        region=_option(raw.region),
        settlement=_option(raw.city),
        region_id=_option_id(raw.region),
        settlement_id=_option_id(raw.city),
        zone=_option(raw.district),
        street=_scrub(_clean_text(_value(raw.street))),
        lat=point.lat if has_point else None,
        lon=point.lon if has_point else None,
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
    """Заменяет похожие на телефон последовательности (8 и более цифр) на заглушку."""

    def replace(match: re.Match[str]) -> str:
        digits = sum(ch.isdigit() for ch in match.group())
        return PHONE_PLACEHOLDER if digits >= 8 else match.group()

    return _PHONE_CANDIDATE.sub(replace, text)


def _scrub(text: str | None) -> str | None:
    return scrub_phones(text) if text is not None else None


def _value(feature):
    return feature.value if feature is not None else None


def _option(feature: Feature[OptionValue] | None) -> str | None:
    option = _value(feature)
    return option.translated if option else None


def _option_id(feature: Feature[OptionValue] | None) -> int | None:
    option = _value(feature)
    value = option.value if option else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value in {"", "-"} else value


def _description(body: BodyValue | None) -> str | None:
    if body is None:
        return None
    return _scrub(_clean_text(body.translated or body.ru or body.ro))
