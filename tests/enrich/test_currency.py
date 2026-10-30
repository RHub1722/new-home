from datetime import date

import httpx
import pytest

from homealize.config import CurrencySettings
from homealize.enrich.currency import ensure_rates, fetch_bnm_rates, to_eur
from homealize.storage import rate_cache

D1 = date(2026, 10, 29)
BNM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ValCurs Date="29.10.2026" name="Official exchange rate">
  <Valute ID="47"><NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal><Name>Euro</Name><Value>20.1303</Value></Valute>
  <Valute ID="44"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>US Dollar</Name><Value>18.0881</Value></Valute>
  <Valute ID="1"><NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal><Name>Yen</Name><Value>11.80</Value></Valute>
</ValCurs>"""


def bnm_client(responses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[min(len(calls), len(responses)) - 1]

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_fetch_parses_rates_and_nominal():
    client, calls = bnm_client([httpx.Response(200, text=BNM_XML)])
    rates = fetch_bnm_rates(client, CurrencySettings(), D1)
    assert rates["EUR"] == 20.1303 and rates["USD"] == 18.0881 and rates["MDL"] == 1.0
    assert rates["JPY"] == pytest.approx(0.118)
    assert calls[0].url.params["date"] == "29.10.2026"
    assert calls[0].url.params["get_xml"] == "1"


def test_answer_without_eur_is_an_error():
    client, _ = bnm_client([httpx.Response(200, text="<ValCurs></ValCurs>")])
    with pytest.raises(ValueError, match="EUR"):
        fetch_bnm_rates(client, CurrencySettings(), D1)


def test_zero_nominal_entry_is_skipped():
    xml = BNM_XML.replace("<Nominal>1</Nominal><Name>US Dollar</Name>", "<Nominal>0</Nominal><Name>US Dollar</Name>")
    client, _ = bnm_client([httpx.Response(200, text=xml)])
    rates = fetch_bnm_rates(client, CurrencySettings(), D1)
    assert rates["EUR"] == 20.1303
    assert "USD" not in rates


@pytest.mark.parametrize("bad_value", ["0", "-1", "nan", "inf", "abc"])
def test_bad_eur_value_is_an_error(bad_value):
    xml = BNM_XML.replace("<Value>20.1303</Value>", f"<Value>{bad_value}</Value>")
    client, _ = bnm_client([httpx.Response(200, text=xml)])
    with pytest.raises(ValueError, match="EUR"):
        fetch_bnm_rates(client, CurrencySettings(), D1)


@pytest.mark.parametrize(
    ("price", "currency", "rates", "expected"),
    [
        (1000.0, "EUR", None, 1000.0),
        (201303.0, "MDL", {"EUR": 20.1303, "MDL": 1.0}, 10000.0),
        (1000.0, "USD", {"EUR": 20.1303, "USD": 18.0881, "MDL": 1.0}, 898.55),
        (1000.0, "GBP", {"EUR": 20.1303, "MDL": 1.0}, None),
        (1000.0, "MDL", None, None),
        (None, "EUR", None, None),
        (1000.0, None, {"EUR": 20.1303}, None),
        (1000.0, "MDL", {"EUR": 0.0, "MDL": 1.0}, None),
    ],
)
def test_to_eur(price, currency, rates, expected):
    assert to_eur(price, currency, rates) == expected


def test_rates_are_cached_per_day(db):
    client, calls = bnm_client([httpx.Response(200, text=BNM_XML)])
    first, warning1 = ensure_rates(db, client, CurrencySettings(), day=D1)
    second, warning2 = ensure_rates(db, client, CurrencySettings(), day=D1)
    assert first == second and first["EUR"] == 20.1303
    assert (warning1, warning2) == (None, None)
    assert len(calls) == 1


def test_failure_uses_latest_cached_day(db):
    client, _ = bnm_client([httpx.Response(200, text=BNM_XML), httpx.Response(500)])
    ensure_rates(db, client, CurrencySettings(), day=D1)
    rates, warning = ensure_rates(db, client, CurrencySettings(), day=date(2026, 10, 30))
    assert rates["EUR"] == 20.1303
    assert "используется курс от 2026-10-29" in warning


def test_failure_without_cache(db):
    client, _ = bnm_client([httpx.Response(500)])
    rates, warning = ensure_rates(db, client, CurrencySettings(), day=D1)
    assert rates is None
    assert "не пересчитаны" in warning


def test_bad_answer_falls_back_without_saving(db):
    bad_xml = BNM_XML.replace("<Value>20.1303</Value>", "<Value>0</Value>")
    client, _ = bnm_client([httpx.Response(200, text=BNM_XML), httpx.Response(200, text=bad_xml)])
    ensure_rates(db, client, CurrencySettings(), day=D1)
    rates, warning = ensure_rates(db, client, CurrencySettings(), day=date(2026, 10, 30))
    assert rates["EUR"] == 20.1303
    assert "используется курс от 2026-10-29" in warning
    assert rate_cache.get_rates(db, "2026-10-30") is None
