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


def page(*listings: Listing, errors: tuple[str, ...] = (), unparsed: tuple[str, ...] = ()) -> Page:
    return Page(
        listings=list(listings),
        total_count=len(listings),
        parse_errors=list(errors),
        unparsed_ids=list(unparsed),
    )


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
