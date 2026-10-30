import pytest

from homealize.geo import haversine_m


def test_one_degree_of_latitude():
    assert haversine_m(47.0, 28.8, 48.0, 28.8) == pytest.approx(111_195, rel=1e-3)


def test_same_point_is_zero():
    assert haversine_m(47.0245, 28.8327, 47.0245, 28.8327) == 0
