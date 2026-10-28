from homealize.models import RunSummary
from tests.factories import make_listing


def test_price_eur_for_euro_listing():
    assert make_listing().price_eur == 28000.0


def test_price_eur_is_none_for_other_currency():
    assert make_listing(price_original=900000.0, currency="MDL").price_eur is None


def test_price_per_sot_is_rounded_to_cents():
    assert make_listing().price_per_sot_eur == 4666.67


def test_price_per_sot_needs_area_and_euro_price():
    assert make_listing(area_sot=None).price_per_sot_eur is None
    assert make_listing(area_sot=0.0).price_per_sot_eur is None
    assert make_listing(price_original=None, currency=None).price_per_sot_eur is None


def test_run_summary_status():
    assert RunSummary(search_complete=True).status == "ok"
    assert RunSummary().status == "failed"
