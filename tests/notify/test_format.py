import json
from zoneinfo import ZoneInfo

from homealize.notify import format as fmt
from tests.factories import score_record

ZONE = ZoneInfo("Europe/Chisinau")


def row(**overrides) -> dict:
    """Строка как у load_scored: снимок оценки плюс поля объявления."""
    base = score_record(
        "123", rank=3, total=78.4, price_eur=32000.0, price_per_sot_eur=4000.0, market_median_eur_per_sot=4800.0,
        route_min=11.2, route_km=9.4, nearest_entry="бул. Дачия (юг)",
    )
    base.update({
        "title": "8 сот.", "url": "https://999.md/ru/123", "region": "Новые Анены", "settlement": "Сынжера",
        "zone": None, "area_sot": 8.0, "price_original": 32000.0, "currency": "EUR", "author_type": "private",
        "first_seen_at": "2026-10-30T05:00:00+00:00", "images": json.dumps(["https://i.example/1.jpg"]),
    })
    base.update(overrides)
    return base


def test_money_area_and_plural():
    assert fmt.money(32000.0) == "32 000 €"
    assert fmt.money(700000.0, "MDL") == "700 000 MDL"
    assert fmt.money(None) == "цена не указана"
    assert (fmt.area(8.0), fmt.area(6.5), fmt.area(None)) == ("8 сот.", "6,5 сот.", "площадь не указана")
    assert [fmt.plural(n, "участок", "участка", "участков") for n in (1, 3, 5, 11, 21)] == [
        "участок", "участка", "участков", "участков", "участок",
    ]


def test_short_takes_the_first_line_and_cuts():
    assert fmt.short("первая\nвторая") == "первая"
    assert fmt.short("x" * 300) == "x" * 199 + "…"


def test_local_time_and_photo():
    assert fmt.local_time("2026-10-30T05:00:00+00:00", ZONE) == "30.10 07:00"
    assert fmt.first_photo(row()) == "https://i.example/1.jpg"
    assert fmt.first_photo(row(images="[]")) is None


def test_listing_line():
    assert fmt.listing_line(row(), fav=True) == "3. ★ 78 · Сынжера, 8 сот., 32 000 € (−17%), 11 мин /ad_123"
    assert fmt.listing_line(row(rank=None, route_min=None), fav=False, note="↑2") == (
        "78 · Сынжера, 8 сот., 32 000 € (−17%) · ↑2 /ad_123"
    )


def test_new_listing_message():
    lines = fmt.new_listing(row(), fav=False).split("\n")
    assert lines[0] == "🆕 <b>Новый участок</b> · балл 78 · место 3"
    assert lines[1] == "Сынжера (Новые Анены), 8 сот."
    assert lines[2] == "32 000 € · 4 000 €/сот. · на 17% дешевле медианы"
    assert lines[3] == "🚗 11 мин до въезда «бул. Дачия (юг)», 9,4 км"
    assert lines[4] == "⚠ нет канализации (не указано в объявлении)"
    assert lines[5] == "Баллы: цена 67 · дорога 80 · инфраструктура 30 · коммуникации 90 · село 50"
    assert lines[6].startswith(
        '<a href="https://999.md/ru/123">999.md</a> · <a href="https://www.openstreetmap.org/?mlat=47.09921&amp;mlon=28.81650'
    )
    assert lines[6].endswith("/ad_123")


def test_values_from_the_site_are_escaped():
    text = fmt.new_listing(row(settlement="<b>Село</b>", region=None), fav=False)
    assert "&lt;b&gt;Село&lt;/b&gt;, 8 сот." in text


def test_city_listing_and_approximate_coordinates():
    text = fmt.new_listing(
        row(nearest_entry="в черте города", route_min=0.0, route_km=0.0, coords_approx=True), fav=False
    )
    assert "🏙 в черте Кишинёва" in text
    assert "карта (центр села)" in text


def test_other_currency_shows_the_euro_price():
    assert fmt.new_listing(row(price_original=640000.0, currency="MDL"), fav=False).split("\n")[2].startswith(
        "640 000 MDL (≈ 32 000 €) · "
    )


def test_price_drop_message():
    text = fmt.price_drop(row(), old="34 000 €", new="32 000 €", percent=6, fav=True)
    lines = text.split("\n")
    assert lines[0] == "📉 <b>Цена снижена</b>: было 34 000 €, стало 32 000 € (−6%)"
    assert lines[1] == "★ Сынжера (Новые Анены), 8 сот. · балл 78 · место 3"


def test_card_shows_reason_explanations_and_price_history():
    history = [{"price_original": 33000.0, "currency": "EUR"}, {"price_original": 32000.0, "currency": "EUR"}]
    text = fmt.card(row(rank=None, excluded_reason="over_budget"), history, fav=False, zone=ZONE)
    assert "Не в рейтинге: дороже бюджета" in text
    assert "Цена 67: на 17% дешевле медианы" in text
    assert "История цены: 33 000 € → 32 000 €" in text
    assert "Автор: частное лицо" in text
    assert "Впервые увидено: 30.10.2026" in text
