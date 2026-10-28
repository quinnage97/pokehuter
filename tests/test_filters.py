from __future__ import annotations

from fastapi.testclient import TestClient
from test_scanner import FakeEbay, run_scan

from pokehunter.filters import clean, passes
from pokehunter.web import create_app

DEAL = {
    "card_name": "Charizard",
    "set_name": "Base Set",
    "local_id": "4",
    "title": "Charizard 4/102 Base Set Holo LP",
    "pokemon": "Charizard",
    "condition": "LP",
    "variant": "holo",
    "total": 150.0,
    "discount_pct": 42.0,
    "feedback_score": 250,
    "priority": 0,
    "photo_verdict": None,
    "ebay_median_gbp": 262.5,
    "shipping_known": 1,
}


def test_clean_keeps_known_settings_only():
    f = clean({"max": "200", "cond": ["LP"], "alerts": 1, "sort": "nonsense", "hack": True})
    assert f["max"] == 200.0 and f["cond"] == ["LP"] and f["alerts"] is True
    assert f["sort"] == "best" and "hack" not in f


def test_passes_matches_each_filter():
    assert passes(DEAL, clean({}))
    assert passes(DEAL, clean({"poke": "Charizard", "cond": ["LP", "NM"], "max": 200}))
    assert not passes(DEAL, clean({"poke": "Mew"}))
    assert not passes(DEAL, clean({"max": 100}))
    assert not passes(DEAL, clean({"min": 200}))
    assert not passes(DEAL, clean({"max": "£99"}))
    assert not passes(DEAL, clean({"max": "149,50"}))
    assert passes(DEAL, clean({"max": " £150 ", "min": "£100"}))
    assert passes(DEAL, clean({"max": "abc"}))  # nonsense means no limit
    assert not passes(DEAL, clean({"disc": 50}))
    assert not passes(DEAL, clean({"vars": ["reverse"]}))
    assert passes({**DEAL, "variant": "holo:set-logo"}, clean({"vars": ["stamped"]}))
    assert not passes(DEAL, clean({"star": True}))
    assert not passes(DEAL, clean({"photo": True}))
    assert not passes(DEAL, clean({"q": "skyridge"}))


def test_saved_filters_round_trip(catalog_db, settings):
    with TestClient(create_app(settings, run_loop=False)) as c:
        assert c.get("/api/filters").json()["alerts"] is False
        saved = c.post("/api/filters", json={"poke": "Mew", "alerts": True}).json()
        assert saved["poke"] == "Mew" and saved["alerts"] is True
        assert c.get("/api/filters").json()["poke"] == "Mew"


def test_alerts_follow_saved_filters_when_asked(catalog_db, settings):
    catalog_db.set_kv("deal_filters", {"poke": "Mew", "alerts": True})
    fake = FakeEbay()
    _, scanner = run_scan(catalog_db, settings, fake)
    assert fake.messages == [] and len(scanner.deals()) == 1  # still shown on the site
    status = catalog_db.one("SELECT alert_status FROM listings WHERE item_id='1'")
    assert status["alert_status"] == "filtered"


def test_filters_dont_touch_alerts_by_default(catalog_db, settings):
    catalog_db.set_kv("deal_filters", {"poke": "Mew"})
    fake = FakeEbay()
    run_scan(catalog_db, settings, fake)
    assert len(fake.messages) == 1
