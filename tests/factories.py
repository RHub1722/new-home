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


def score_record(listing_id: str, **overrides) -> dict:
    """Строка снимка scores для тестов: в рейтинге на 1-м месте, балл 70; поля можно переопределить."""
    base = {
        "listing_id": listing_id,
        "computed_at": "2026-10-30T05:00:00+00:00",
        "price_eur": 28000.0,
        "price_per_sot_eur": 4666.67,
        "lat": 47.09921,
        "lon": 28.8165,
        "coords_approx": False,
        "route_km": 8.0,
        "route_min": 10.0,
        "nearest_entry": "M3 Drumul Băcioiului",
        "poi_nearest_m": "{}",
        "market_median_eur_per_sot": 5600.0,
        "median_level": "settlement",
        "median_n": 6,
        "duplicate_group": None,
        "is_group_primary": True,
        "components": json.dumps(
            {"price": 66.7, "route": 80.0, "infrastructure": 30.0, "utilities": 90.0, "settlement": 50.0}
        ),
        "explanations": json.dumps(
            {"price": "Цена 67: на 17% дешевле медианы", "route": "Дорога 80: 10 мин до въезда"}, ensure_ascii=False
        ),
        "flags": json.dumps(["no_sewerage"]),
        "total": 70.0,
        "excluded_reason": None,
        "rank": 1,
    }
    base.update(overrides)
    return base


class FakeTelegram:
    """Telegram для тестов: запоминает отправленное.

    errors — исключения для первых отправок по порядку; updates — ответы get_updates по порядку
    (список обновлений или исключение).
    """

    def __init__(self, *, errors=(), updates=()):
        self.sent: list[tuple[int, str, str | None]] = []
        self.errors = list(errors)
        self.updates = list(updates)
        self.polls: list[tuple[int | None, int]] = []
        self.closed = False

    def send_message(self, chat_id, text, *, preview_url=None):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((chat_id, text, preview_url))

    def get_updates(self, offset, timeout):
        self.polls.append((offset, timeout))
        if not self.updates:
            return []
        batch = self.updates.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch

    def get_me(self):
        return {"username": "homealize_test_bot"}

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def texts(self) -> list[str]:
        return [text for _chat, text, _preview in self.sent]
