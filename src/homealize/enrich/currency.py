"""Официальный курс Нацбанка Молдовы (BNM) и перевод цен в евро."""

import math
import sqlite3
from datetime import date
from xml.etree import ElementTree

import httpx

from homealize.config import CurrencySettings
from homealize.enrich.errors import describe_error
from homealize.storage import rate_cache


def fetch_bnm_rates(client: httpx.Client, settings: CurrencySettings, day: date) -> dict[str, float]:
    """Курсы на день: сколько леев стоит единица валюты."""
    response = client.get(settings.bnm_url, params={"get_xml": "1", "date": day.strftime("%d.%m.%Y")},
                          timeout=settings.timeout_seconds)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    rates = {"MDL": 1.0}
    for valute in root.iter("Valute"):
        code = valute.findtext("CharCode")
        try:
            value = float(valute.findtext("Value") or "")
            nominal = float(valute.findtext("Nominal") or "1")
        except ValueError:
            continue  # нечисловое значение: такую запись пропускаем
        if code and nominal > 0:
            rate = value / nominal
            if math.isfinite(rate) and rate > 0:
                rates[code] = rate
    if "EUR" not in rates:
        raise ValueError("в ответе BNM нет курса EUR")
    return rates


def ensure_rates(
    conn: sqlite3.Connection, client: httpx.Client, settings: CurrencySettings, *, day: date
) -> tuple[dict[str, float] | None, str | None]:
    """Курс на день из кэша или BNM; при сбое — последний сохранённый."""
    key = day.isoformat()
    cached = rate_cache.get_rates(conn, key)
    if cached:
        return cached, None
    try:
        rates = fetch_bnm_rates(client, settings, day)
    except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
        message = f"Курс BNM не получен ({describe_error(exc)})"
        latest = rate_cache.latest_rates(conn)
        if latest:
            return latest[1], f"{message}, используется курс от {latest[0]}"
        return None, f"{message}, цены не в евро не пересчитаны"
    rate_cache.put_rates(conn, key, rates)
    conn.commit()
    return rates, None


def to_eur(price: float | None, currency: str | None, rates: dict[str, float] | None) -> float | None:
    if price is None or currency is None:
        return None
    if currency == "EUR":
        return price
    if not rates or currency not in rates or not rates.get("EUR", 0.0) > 0:
        return None
    return round(price * rates[currency] / rates["EUR"], 2)
