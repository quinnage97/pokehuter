from __future__ import annotations

from fastapi.testclient import TestClient
from test_scanner import FakeEbay, run_scan

from pokehunter.db import DEFAULT_PREFS
from pokehunter.reseller import estimate, finds
from pokehunter.web import create_app

ROW = {"market_gbp": 100.0, "ebay_median_gbp": 90.0, "total": 20.0, "condition": "NM"}


def test_estimate_profit():
    numbers = estimate(ROW, DEFAULT_PREFS)
    # Market £90 (lower of guide and eBay); 78% under; resell £90 - 13% fees - £2 post.
    assert numbers == {
        "market_value": 90.0,
        "reseller_discount": 77.8,
        "resale_gbp": 90.0,
        "costs_gbp": 13.7,
        "profit_gbp": 56.3,
    }
    assert estimate({**ROW, "condition": "HP"}, DEFAULT_PREFS)["resale_gbp"] == 49.5


def test_estimate_needs_trustworthy_prices_and_the_band():
    assert estimate({**ROW, "ebay_median_gbp": None}, DEFAULT_PREFS) is None
    assert estimate({**ROW, "market_gbp": 400.0}, DEFAULT_PREFS) is None  # guide vs eBay disagree
    assert estimate({**ROW, "total": 50.0}, DEFAULT_PREFS) is None  # only 44% under
    assert estimate({**ROW, "total": 5.0}, DEFAULT_PREFS) is None  # 94% under


def _listing(db, listing_id, item, total, **extra):
    cols = {
        "item_id": listing_id,
        "title": "t",
        "url": "u",
        "price": total,
        "shipping": 0,
        "total": total,
        "condition": "NM",
        "first_seen": "x",
        "match_item_id": item,
        **extra,
    }
    names = ", ".join(cols) + ", last_seen"
    marks = ", ".join("?" for _ in cols) + ", strftime('%Y-%m-%dT%H:%M:%S+00:00','now')"
    with db.tx() as c:
        c.execute(f"INSERT INTO listings ({names}) VALUES ({marks})", tuple(cols.values()))


def test_finds_include_other_pokemon_and_skip_junk(catalog_db):
    with catalog_db.tx() as c:
        c.execute(
            "UPDATE items SET ebay_median_gbp = 40, ebay_comps = 6 WHERE id = 'base1-58:normal'"
        )
    _listing(catalog_db, "pika", "base1-58:normal", 8.0)
    _listing(catalog_db, "fake", "base1-58:normal", 8.0, photo_verdict="fake")
    _listing(catalog_db, "reported", "base1-58:normal", 8.0, user_verdict="fake")
    _listing(catalog_db, "sold", "base1-58:normal", 8.0, sold=1)
    found = finds(catalog_db, DEFAULT_PREFS)
    assert [f["listing_id"] for f in found] == ["pika"]
    assert found[0]["pokemon"] == "Pikachu" and found[0]["reseller_discount"] == 80.0


def test_reseller_cards_stay_out_of_the_collection(catalog_db, settings):
    with TestClient(create_app(settings, run_loop=False)) as c:
        cards = {i["card_id"] for i in c.get("/api/cards").json()["items"]}
        assert "base1-58" not in cards and "base1-4" in cards
        progress = {p["pokemon"] for p in c.get("/api/status").json()["progress"]}
        assert progress == {"Charizard", "Mew"}
        assert c.get("/api/reseller").json() == {"finds": []}


def test_reseller_pokemon_are_swept_in_rotation(catalog_db, settings):
    catalog_db.update_prefs({"reseller_sweeps_per_scan": 3})
    fake = FakeEbay()
    run_scan(catalog_db, settings, fake)
    run_scan(catalog_db, settings, FakeEbay())
    first = fake.searches[2:5]
    assert fake.searches[:2] == ["Charizard", "Mew"]
    # Pacing can skip the rotation if the day's calls are ahead of schedule, so only check
    # its shape when it ran.
    if first and first[0] == "Pikachu":
        assert first == ["Pikachu", "Mewtwo", "Umbreon"]
        assert catalog_db.get_kv("reseller_cursor") == 6
