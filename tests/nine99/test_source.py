import json

import httpx
import pytest

from homealize.config import Settings
from homealize.sources.base import EmptySearchError, SourceSchemaError
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


def test_first_page_without_ads_raises_empty_search():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"searchAds": {"ads": [], "count": 500}}})

    source = make_source(handler)
    with pytest.raises(EmptySearchError):
        list(source.iter_pages())
    assert len(requests) == 1


def test_empty_page_mid_scan_raises_and_keeps_earlier_pages():
    def handler(request):
        skip = json.loads(request.content)["variables"]["input"]["pagination"]["skip"]
        if skip == 0:
            return httpx.Response(200, json=load_fixture("search_page_1.json"))
        return httpx.Response(200, json={"data": {"searchAds": {"ads": [], "count": 6}}})

    source = make_source(handler)
    pages = []
    with pytest.raises(SourceSchemaError):
        for page in source.iter_pages():
            pages.append(page)
    assert len(pages) == 1
    assert len(pages[0].listings) == 4


def test_empty_page_after_count_shrank_ends_the_scan():
    def handler(request):
        skip = json.loads(request.content)["variables"]["input"]["pagination"]["skip"]
        if skip == 0:
            return httpx.Response(200, json=load_fixture("search_page_1.json"))
        return httpx.Response(200, json={"data": {"searchAds": {"ads": [], "count": 3}}})

    pages = list(make_source(handler).iter_pages())
    assert len(pages) == 1


def test_source_is_a_context_manager():
    source = make_source(fixture_handler([]))
    with source as same:
        assert same is source
    assert source._client._client.is_closed


def test_empty_page_with_zero_count_mid_scan_raises():
    def handler(request):
        skip = json.loads(request.content)["variables"]["input"]["pagination"]["skip"]
        if skip == 0:
            return httpx.Response(200, json=load_fixture("search_page_1.json"))
        return httpx.Response(200, json={"data": {"searchAds": {"ads": [], "count": 0}}})

    source = make_source(handler)
    pages = []
    with pytest.raises(SourceSchemaError):
        for page in source.iter_pages():
            pages.append(page)
    assert len(pages) == 1


def page_1_payload(*, ads: int = 4, count: int = 6) -> dict:
    payload = load_fixture("search_page_1.json")
    payload["data"]["searchAds"]["ads"] = payload["data"]["searchAds"]["ads"][:ads]
    payload["data"]["searchAds"]["count"] = count
    return payload


def test_count_smaller_than_received_raises_schema_error():
    source = make_source(lambda request: httpx.Response(200, json=page_1_payload(count=2)))
    with pytest.raises(SourceSchemaError, match="найдено 2"):
        list(source.iter_pages())


def test_short_page_mid_scan_raises_and_mentions_page_size():
    source = make_source(lambda request: httpx.Response(200, json=page_1_payload(ads=3, count=6)))
    pages = []
    with pytest.raises(SourceSchemaError, match="page_size"):
        for page in source.iter_pages():
            pages.append(page)
    assert pages == []


def test_single_short_page_that_covers_the_count_is_fine():
    source = make_source(lambda request: httpx.Response(200, json=page_1_payload(ads=3, count=3)))
    pages = list(source.iter_pages())
    assert [len(page.listings) for page in pages] == [3]


def test_build_from_settings_sends_russian_headers():
    requests = []

    def handler(request):
        requests.append(request)
        payload = load_fixture("search_page_2.json")
        payload["data"]["searchAds"]["count"] = 2  # одна страница из двух объявлений: сервер согласован
        return httpx.Response(200, json=payload)

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
