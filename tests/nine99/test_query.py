from homealize.sources.nine99.query import SEARCH_QUERY, build_search_body


def test_search_body_has_filters_and_pagination():
    body = build_search_body(skip=78, limit=78, area_min=5, area_max=10)
    search = body["variables"]["input"]
    assert body["operationName"] == "SearchAds"
    assert body["query"] == SEARCH_QUERY
    assert body["variables"]["locale"] == "ru_RU"
    assert search["pagination"] == {"limit": 78, "skip": 78}
    assert search["subCategoryId"] == 1407
    assert search["sort"] == "SORT_ADS_DATE_DESC"
    offer, land_type, area = search["filters"]
    assert offer == {"filterId": 16, "features": [{"featureId": 1, "optionIds": [776]}]}
    assert land_type == {"filterId": 42, "features": [{"featureId": 258, "optionIds": [1039]}]}
    assert area["filterId"] == 1211
    assert area["features"][0] == {"featureId": 245, "range": {"min": "5", "max": "10"}, "unit": "UNIT_ARE"}


def test_fractional_area_bounds_are_kept():
    body = build_search_body(skip=0, limit=10, area_min=5.5, area_max=10)
    area = body["variables"]["input"]["filters"][2]
    assert area["features"][0]["range"] == {"min": "5.5", "max": "10"}


def test_query_never_requests_seller_contacts():
    assert "feature(id: 16)" not in SEARCH_QUERY
    assert "owner" not in SEARCH_QUERY
    assert "phone" not in SEARCH_QUERY.lower()
