"""Источник объявлений 999.md: постраничный обход поиска SearchAds."""

import logging
from collections.abc import Iterator

import httpx

from homealize.config import Settings
from homealize.sources.base import EmptySearchError, Page, SourceSchemaError
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
            received = len(page.listings) + len(page.parse_errors)
            if received == 0:
                if skip == 0:
                    raise EmptySearchError("SearchAds вернул 0 объявлений")
                raise SourceSchemaError(f"999.md вернул пустую страницу с {skip} (найдено {page.total_count})")
            log.info(
                "999.md: страница с %s, объявлений %s из %s", skip, len(page.listings), page.total_count
            )
            yield page
            skip += self._page_size
            if skip >= page.total_count:
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
