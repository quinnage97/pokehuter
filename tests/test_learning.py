from __future__ import annotations

from fastapi.testclient import TestClient
from test_scanner import FakeEbay, run_scan

from pokehunter.learning import learn, record_fake, title_words, undo_fake
from pokehunter.web import create_app


def add_listing(db, listing_id, title, seller, total, fair=200.0, item="base1-4:holo"):
    with db.tx() as c:
        c.execute(
            "INSERT INTO listings (item_id, title, url, price, shipping, total, condition,"
            " seller, first_seen, last_seen, match_item_id, fair_gbp)"
            " VALUES (?, ?, 'u', ?, 0, ?, '?', ?, 'x', strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),"
            " ?, ?)",
            (listing_id, title, total, total, seller, item, fair),
        )


def test_reports_teach_sellers_words_and_price_traps(catalog_db):
    add_listing(catalog_db, "f1", "Charizard 4/102 sp250 holo", "faker1", 40)
    add_listing(catalog_db, "f2", "Charizard 4/102 SP250 gold", "faker2", 190)
    for n in range(5):
        add_listing(catalog_db, f"g{n}", "Charizard 4/102 Base Set Holo", f"good{n}", 300)
    record_fake(catalog_db, "f1", "Gold metal card")
    record_fake(catalog_db, "f2", "")
    signals = learn(catalog_db)
    assert signals.sellers == {"faker1", "faker2"}
    assert "sp250" in signals.words and "charizard" not in signals.words
    # Only the clearly underpriced fake (£40 vs £200 fair) sets a price trap.
    assert signals.price_traps == {"base1-4:holo": 40}
    assert signals.flag("base1-4:holo", "Charizard 4/102", "someone", 45)
    assert signals.flag("base1-4:holo", "Charizard 4/102", "someone", 150) is None
    assert signals.flag("x:holo", "Charizard sp250", "someone", 500)
    assert signals.flag("x:holo", "Charizard", "Faker1", 500)
    assert signals.reasons["base1-4"] == ["Gold metal card", "Marked as fake"]


def test_card_name_words_are_never_learned(catalog_db):
    add_listing(catalog_db, "f1", "Vivid Voltage Charizard fake", "a", 1)
    add_listing(catalog_db, "f2", "Vivid Voltage Charizard copy", "b", 1)
    record_fake(catalog_db, "f1", "")
    record_fake(catalog_db, "f2", "")
    assert not {"vivid", "voltage"} & learn(catalog_db).words


def test_undo_restores_listing(catalog_db):
    add_listing(catalog_db, "f1", "Charizard 4/102", "faker", 40)
    report_id = record_fake(catalog_db, "f1", "")
    undo_fake(catalog_db, report_id)
    assert learn(catalog_db).sellers == set()
    assert catalog_db.one("SELECT dismissed FROM listings WHERE item_id='f1'")["dismissed"] == 0


def test_reported_seller_blocks_future_deals(catalog_db, settings):
    _, scanner = run_scan(catalog_db, settings, FakeEbay())
    assert [d["listing_id"] for d in scanner.deals()] == ["1"]
    add_listing(catalog_db, "other", "Charizard 4/102 junk", "seller", 5)
    record_fake(catalog_db, "other", "")  # same seller name as the fixture listings
    _, scanner = run_scan(catalog_db, settings, FakeEbay())
    assert scanner.deals() == []
    row = catalog_db.one("SELECT warning FROM listings WHERE item_id='1'")
    assert "Likely fake" in row["warning"]


def test_title_words():
    assert "sp250" in title_words("Pokemon Charizard SP250 holo")
    assert "pokemon" not in title_words("Pokemon Charizard")


def test_fake_and_basket_api(catalog_db, settings):
    run_scan(catalog_db, settings, FakeEbay())
    with TestClient(create_app(settings, run_loop=False)) as c:
        basket = c.post("/api/basket", json={"listing_ids": ["1", "2", "nope"]}).json()
        assert [i["listing_id"] for i in basket["items"]] == ["1", "2"]
        assert basket["items"][0]["variant_label"] == "Holo"
        cards = c.get("/api/cards").json()["items"]
        holo = next(i for i in cards if i["id"] == "base1-4:holo")
        assert holo["cheapest_total"] == 150.0 and holo["cheapest_listing"] == "1"

        learned = c.post("/api/listings/2/fake", json={"reason": "Wrong font"}).json()
        assert learned["sellers"] == ["seller"] and learned["reports"][0]["reason"] == "Wrong font"
        assert [i["listing_id"] for i in c.get("/api/basket").json()["items"]] == ["1"]
        assert c.post("/api/listings/missing/fake", json={}).status_code == 404

        undone = c.post(f"/api/learning/undo/{learned['id']}").json()
        assert undone["reports"] == []

        assert c.post("/api/basket/bought").json() == {"items": []}
        cards = c.get("/api/cards").json()["items"]
        assert next(i for i in cards if i["id"] == "base1-4:holo")["owned"] == 1
