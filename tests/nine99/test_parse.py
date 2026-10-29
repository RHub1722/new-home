from datetime import datetime, timedelta, timezone

import pytest

from homealize.sources.base import SourceSchemaError
from homealize.sources.nine99.parse import IMAGE_URL, parse_search_response, scrub_phones
from tests.factories import load_fixture

CHISINAU = timezone(timedelta(hours=2))


def by_id(name: str) -> dict:
    return {listing.id: listing for listing in parse_search_response(load_fixture(name)).listings}


def test_page_1_parses_all_ads_without_errors():
    page = parse_search_response(load_fixture("search_page_1.json"))
    assert page.total_count == 6
    assert [listing.id for listing in page.listings] == ["100000001", "100000002", "100000003", "100000004"]
    assert page.parse_errors == []


def test_full_ad_is_normalized():
    ad = by_id("search_page_1.json")["100000001"]
    assert ad.source == "999"
    assert ad.url == "https://999.md/ru/100000001"
    assert ad.title == "6 сот., участок под строительство"
    assert ad.author_type == "private"
    assert (ad.region, ad.settlement, ad.zone) == ("Кишинёв мун.", "Гратиешты", "Окраина")
    assert ad.street is None
    assert (ad.lat, ad.lon) == (47.09921, 28.8165)
    assert ad.area_sot == 6.0
    assert ad.land_type == "Участок под строительство"
    assert (ad.price_original, ad.currency, ad.price_eur) == (28000.0, "EUR", 28000.0)
    assert (ad.has_gas, ad.has_electricity, ad.has_water, ad.has_sewerage) == (True, True, True, False)
    assert (ad.has_asphalt_road, ad.in_settlement) == (False, True)
    assert ad.images == (IMAGE_URL.format(name="aaa111.jpg"), IMAGE_URL.format(name="bbb222.jpg"))
    assert ad.site_updated_at == datetime(2026, 10, 28, 23, 15, tzinfo=CHISINAU)


def test_phone_in_description_is_removed():
    ad = by_id("search_page_1.json")["100000001"]
    assert ad.description == "Ровный участок, все коммуникации. Звоните [телефон удалён]"


def test_ad_without_map_and_utilities_section():
    ad = by_id("search_page_1.json")["100000002"]
    assert ad.author_type == "agency"
    assert (ad.lat, ad.lon) == (None, None)
    assert (ad.has_gas, ad.has_electricity, ad.has_water, ad.has_sewerage) == (None, None, None, None)
    assert ad.in_settlement is None
    assert ad.street == "str. Alexandru cel Bun"
    assert ad.description is None
    assert ad.images == ()
    assert (ad.price_original, ad.currency, ad.price_eur) == (900000.0, "MDL", None)


def test_map_point_without_coordinates_gives_no_coords():
    payload = load_fixture("search_page_1.json")
    payload["data"]["searchAds"]["ads"][0]["mapPoint"] = {"value": {"zoom": 6}}
    page = parse_search_response(payload)
    assert page.parse_errors == []
    ad = page.listings[0]
    assert (ad.lat, ad.lon) == (None, None)


def test_map_point_with_only_lat_gives_no_coords():
    payload = load_fixture("search_page_1.json")
    payload["data"]["searchAds"]["ads"][0]["mapPoint"] = {"value": {"lat": 47.0, "zoom": 6}}
    page = parse_search_response(payload)
    assert page.parse_errors == []
    ad = page.listings[0]
    assert (ad.lat, ad.lon) == (None, None)


def test_developer_author():
    assert by_id("search_page_1.json")["100000003"].author_type == "developer"


def test_broken_ad_is_reported_and_others_kept():
    page = parse_search_response(load_fixture("search_page_2.json"))
    assert [listing.id for listing in page.listings] == ["100000006"]
    assert len(page.parse_errors) == 1
    assert "100000005" in page.parse_errors[0]


def test_unparsable_ad_id_is_reported_in_unparsed_ids():
    page = parse_search_response(load_fixture("search_page_2.json"))
    assert page.unparsed_ids == ["100000005"]


def test_parse_error_text_has_location_but_no_input_values():
    page = parse_search_response(load_fixture("search_page_2.json"))
    text = page.parse_errors[0]
    assert "100000005" in text
    assert "mapPoint" in text
    assert "north" not in text


def test_option_sub_field_drift_does_not_make_ad_unparsable():
    payload = load_fixture("search_page_1.json")
    payload["data"]["searchAds"]["ads"][0]["region"]["value"]["value"] = "x"
    page = parse_search_response(payload)
    assert page.parse_errors == []
    assert page.unparsed_ids == []
    assert page.listings[0].region == "Кишинёв мун."


def test_ad_without_id_is_reported_but_has_no_unparsed_id():
    payload = load_fixture("search_page_1.json")
    del payload["data"]["searchAds"]["ads"][0]["id"]
    page = parse_search_response(payload)
    assert len(page.parse_errors) == 1
    assert page.unparsed_ids == []


def test_missing_price_is_none():
    ad = by_id("search_page_2.json")["100000006"]
    assert (ad.price_original, ad.currency) == (None, None)


def test_unknown_area_unit_gives_no_area():
    payload = load_fixture("search_page_1.json")
    payload["data"]["searchAds"]["ads"][0]["landArea"]["value"]["unit"] = "UNIT_HECTARE"
    assert parse_search_response(payload).listings[0].area_sot is None


def test_graphql_errors_raise_schema_error():
    with pytest.raises(SourceSchemaError, match="GraphQL"):
        parse_search_response({"errors": [{"message": "Cannot query field"}], "data": None})


def test_unexpected_structure_raises_schema_error():
    with pytest.raises(SourceSchemaError):
        parse_search_response({"data": {"somethingElse": {}}})


def test_envelope_error_text_has_no_input_values():
    payload = {"data": {"searchAds": {"ads": "secret-text", "count": 1}}}
    with pytest.raises(SourceSchemaError) as caught:
        parse_search_response(payload)
    assert "secret-text" not in str(caught.value)
    assert "searchAds" in str(caught.value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Звоните +373 60 000 000", "Звоните [телефон удалён]"),
        ("тел. 060000000, Иван", "тел. [телефон удалён], Иван"),
        ("тел 079-00-00-00", "тел [телефон удалён]"),
        ("Цена 45 000 €, торг", "Цена 45 000 €, торг"),
        ("Участок 8 соток, 2026 год", "Участок 8 соток, 2026 год"),
        ("Цена 1 200 000 MDL", "Цена 1 200 000 MDL"),
        ("Тел: 069 123 456 079 123 456", "Тел: [телефон удалён]"),
        ("Тел: 069123456 - 079123456", "Тел: [телефон удалён]"),
        ("Цена 15000 069123456", "Цена [телефон удалён]"),
    ],
)
def test_scrub_phones(text, expected):
    assert scrub_phones(text) == expected
