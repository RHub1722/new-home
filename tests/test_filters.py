import pytest

from homealize.config import FilterSettings
from homealize.filters import filter_reason
from tests.factories import make_listing

RULES = FilterSettings()


def test_good_listing_passes():
    assert filter_reason(make_listing(), RULES) is None


def test_wrong_land_type():
    assert filter_reason(make_listing(land_type="Участок под с/х"), RULES) == "land_type"


@pytest.mark.parametrize("area", [5.0, 7.5, 10.0])
def test_area_bounds_are_inclusive(area):
    assert filter_reason(make_listing(area_sot=area), RULES) is None


@pytest.mark.parametrize("area", [4.9, 10.1, 12.0, None])
def test_area_outside_range(area):
    assert filter_reason(make_listing(area_sot=area), RULES) == "area"


def test_region_outside_list():
    assert filter_reason(make_listing(region="Кагул"), RULES) == "region"


def test_unknown_region_passes():
    assert filter_reason(make_listing(region=None), RULES) is None


def test_missing_price():
    assert filter_reason(make_listing(price_original=None, currency=None), RULES) == "no_price"
