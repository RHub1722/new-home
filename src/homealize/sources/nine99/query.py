"""GraphQL-запрос поиска участков на 999.md.

Номера полей сняты с сайта 2026-10-29 (спека §3). Поле feature(id: 16), контакты
продавца, и поле owner намеренно НЕ запрашиваются.
"""

SEARCH_QUERY = """
query SearchAds($input: Ads_SearchInput!, $locale: Common_Locale) {
  searchAds(input: $input) {
    ads {
      id
      title
      reseted(input: {format: "2006-01-02T15:04:05Z07:00", locale: $locale, timezone: "Europe/Chisinau", getDiff: false})
      price: feature(id: 2) { value }
      images: feature(id: 14) { value }
      body: feature(id: 13) { value }
      author: feature(id: 795) { value }
      landType: feature(id: 258) { value }
      landArea: feature(id: 245) { value }
      mapPoint: feature(id: 3) { value }
      region: feature(id: 7) { value }
      city: feature(id: 8) { value }
      district: feature(id: 9) { value }
      street: feature(id: 10) { value }
      gas: feature(id: 168) { value }
      electricity: feature(id: 236) { value }
      water: feature(id: 165) { value }
      sewerage: feature(id: 181) { value }
      asphaltRoad: feature(id: 155) { value }
      inSettlement: feature(id: 209) { value }
    }
    count
  }
}
"""

LAND_SUBCATEGORY_ID = 1407
OFFER_SELL = {"filterId": 16, "features": [{"featureId": 1, "optionIds": [776]}]}
LAND_FOR_CONSTRUCTION = {"filterId": 42, "features": [{"featureId": 258, "optionIds": [1039]}]}


def build_search_body(*, skip: int, limit: int, area_min: float, area_max: float) -> dict:
    """Тело POST /graphql для одной страницы поиска (новые объявления первыми)."""
    area = {
        "filterId": 1211,
        "features": [
            {"featureId": 245, "range": {"min": _number(area_min), "max": _number(area_max)}, "unit": "UNIT_ARE"}
        ],
    }
    return {
        "operationName": "SearchAds",
        "variables": {
            "input": {
                "source": "AD_SOURCE_DESKTOP_REDESIGN",
                "sort": "SORT_ADS_DATE_DESC",
                "pagination": {"limit": limit, "skip": skip},
                "subCategoryId": LAND_SUBCATEGORY_ID,
                "filters": [OFFER_SELL, LAND_FOR_CONSTRUCTION, area],
            },
            "locale": "ru_RU",
        },
        "query": SEARCH_QUERY,
    }


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
