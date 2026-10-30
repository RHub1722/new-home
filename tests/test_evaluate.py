from datetime import UTC, datetime
from pathlib import Path

import httpx

from homealize.config import Settings
from homealize.evaluate import evaluate
from homealize.storage import repo
from tests.factories import make_listing

T0 = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = Settings.model_validate(
    {"scoring": {"settlements_path": str(PROJECT_ROOT / "config" / "settlements.yaml")}}
)
OVERPASS = {"elements": [{"type": "node", "id": 1, "lat": 46.931, "lon": 28.94, "tags": {"shop": "supermarket"}}]}
BNM_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><ValCurs Date="30.10.2026">'
    "<Valute ID=\"47\"><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>20.1303</Value></Valute></ValCurs>"
)


def services_handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "overpass-api.de":
            return httpx.Response(200, json=OVERPASS)
        if request.url.host == "www.bnm.md":
            return httpx.Response(200, text=BNM_XML)
        if request.url.host == "router.project-osrm.org":
            n = len(request.url.params["sources"].split(";"))
            k = len(request.url.params["destinations"].split(";"))
            return httpx.Response(200, json={"code": "Ok", "durations": [[600.0] * k] * n,
                                              "distances": [[8000.0] * k] * n})
        return httpx.Response(404)

    return handler


def add_three(db):
    for i in range(3):
        repo.upsert_listing(
            db,
            make_listing(id=f"x{i}", lat=46.93 + i * 0.01, lon=28.94, settlement_id=14398, settlement="Сынжера"),
            status="active", filter_reason=None, now=T0,
        )
    db.commit()


def test_evaluate_scores_listings_and_reuses_caches(db):
    add_three(db)
    calls = []
    http = httpx.Client(transport=httpx.MockTransport(services_handler(calls)))
    summary = evaluate(db, SETTINGS, http, now=T0, sleep=lambda seconds: None)
    assert summary.warnings == []
    assert (summary.scored, summary.eligible) == (3, 3)
    assert sorted(set(calls)) == ["overpass-api.de", "router.project-osrm.org", "www.bnm.md"]
    calls.clear()
    evaluate(db, SETTINGS, http, now=T0, sleep=lambda seconds: None)
    assert calls == []


def test_service_failures_become_warnings(db):
    add_three(db)
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    summary = evaluate(db, SETTINGS, http, now=T0, sleep=lambda seconds: None)
    assert len(summary.warnings) == 3
    assert any("POI не обновлены" in w for w in summary.warnings)
    assert any("Курс BNM не получен" in w for w in summary.warnings)
    assert any("Маршруты не посчитаны" in w for w in summary.warnings)
    assert (summary.scored, summary.eligible) == (3, 0)
