"""Жёсткие фильтры этапа 1: что вообще считается подходящим участком."""

from homealize.config import FilterSettings
from homealize.models import Listing


def filter_reason(listing: Listing, rules: FilterSettings) -> str | None:
    """Причина, по которой объявление не проходит фильтры, или None, если проходит."""
    if listing.land_type != rules.land_type:
        return "land_type"
    if listing.area_sot is None or not rules.area_sot_min <= listing.area_sot <= rules.area_sot_max:
        return "area"
    if listing.region is not None and listing.region not in rules.allowed_regions:
        return "region"
    if listing.price_original is None:
        return "no_price"
    return None
