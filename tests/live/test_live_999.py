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
