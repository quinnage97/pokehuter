from __future__ import annotations

import asyncio

import httpx
from conftest import ebay_item

from pokehunter.alerts import Telegram
from pokehunter.ebay import EbayClient, parse_item
from pokehunter.scanner import Scanner, evaluate, search_query

LISTINGS = [
    # Base Charizard, LP: fair = 304 * 0.85 = 258.40. £150 delivered is 42% under.
    ebay_item("1", "Charizard 4/102 Base Set Holo LP", "149.00"),
    # Fairly priced copies, which also confirm the guide price is realistic on eBay.
    ebay_item("2", "Charizard 4/102 Base Set Holo NM", "330.00"),
    ebay_item("6", "Charizard 4/102 Base Set Unlimited Holo Rare", "299.00"),
    ebay_item("7", "Pokemon Charizard 4/102 Holo Base", "309.00"),
    # Mewtwo, ignored.
    ebay_item("3", "Mewtwo 114/264 Fusion Strike", "0.50"),
    # Multi-variation listing, ignored.
    ebay_item("4", "Charizard 4/102", "1.00", itemGroupType="SELLER_DEFINED_VARIATIONS"),
    # HP copy of a cheap card: played copies only accepted above £15.
    ebay_item("5", "Charizard 025/185 Vivid Voltage Reverse HP", "0.20", shipping="0"),
]


class FakeEbay:
    def __init__(self):
        self.messages = []
        self.searches = []

    def __call__(self, request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        if "item_summary/search" in url:
            self.searches.append(request.url.params["q"])
            return httpx.Response(200, json={"itemSummaries": LISTINGS})
        if "api.telegram.org" in url:
            self.messages.append(request.content.decode())
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)


def run_scan(db, settings, fake):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
            scanner = Scanner(
                db, settings, EbayClient(settings, http, db), Telegram(settings, http)
            )
            return await scanner.scan(), scanner

    return asyncio.run(go())


def test_scan_finds_deal_and_alerts_once(catalog_db, settings):
    fake = FakeEbay()
    result, scanner = run_scan(catalog_db, settings, fake)
    assert result["errors"] == []
    deals = scanner.deals()
    assert [d["listing_id"] for d in deals] == ["1"]
    assert deals[0]["fair_gbp"] == 258.4 and deals[0]["total"] == 150.0
    assert deals[0]["ebay_median_gbp"] == 305.0 and deals[0]["ebay_comps"] == 4
    assert len(fake.messages) == 1 and "Charizard" in fake.messages[0]
    # Sweep queries per Pokémon plus targeted card searches.
    assert fake.searches[:2] == ["Charizard", "Mew"]
    assert "Charizard 4/102" in fake.searches

    run_scan(catalog_db, settings, fake)
    assert len(fake.messages) == 1  # no repeat alert


def test_owned_cards_are_filtered_out(catalog_db, settings):
    with catalog_db.tx() as c:
        c.execute("UPDATE items SET owned=1 WHERE id='base1-4:holo'")
    fake = FakeEbay()
    _, scanner = run_scan(catalog_db, settings, fake)
    assert scanner.deals() == [] and fake.messages == []
    # The 1st Edition is still wanted, so the card is still searched...
    assert "Charizard 4/102" in fake.searches
    with catalog_db.tx() as c:
        c.execute("UPDATE items SET owned=1 WHERE card_id='base1-4'")
    fake = FakeEbay()
    run_scan(catalog_db, settings, fake)
    assert "Charizard 4/102" not in fake.searches  # ...until every version is owned


def test_priority_cards_searched_first(catalog_db, settings):
    with catalog_db.tx() as c:
        c.execute("UPDATE items SET priority=1 WHERE card_id='swsh8-114'")
    targets = Scanner(catalog_db, settings, None, None).pick_targets(1)
    assert targets[0]["id"] == "swsh8-114"


def test_target_price_triggers_deal(catalog_db, settings):
    with catalog_db.tx() as c:
        c.execute("UPDATE items SET target_price=340 WHERE id='base1-4:holo'")
    _, scanner = run_scan(catalog_db, settings, FakeEbay())
    assert {d["listing_id"] for d in scanner.deals()} == {"1", "2", "6", "7"}


def test_alerts_respect_settings(catalog_db, settings):
    catalog_db.update_prefs({"alerts_enabled": False})
    fake = FakeEbay()
    _, scanner = run_scan(catalog_db, settings, fake)
    assert fake.messages == [] and len(scanner.deals()) == 1


def test_evaluate_rules():
    prefs = {
        "min_discount_pct": 20,
        "priority_discount_pct": 5,
        "min_saving_gbp": 2,
        "played_ok_above_gbp": 15,
        "allow_damaged": False,
        "min_seller_feedback": 5,
        "trusted_seller_above_gbp": 30,
        "trusted_seller_feedback": 50,
        "suspicious_above_pct": 50,
    }
    item = {"market_gbp": 100.0, "target_price": None, "priority": 0}
    listing = parse_item(ebay_item("x", "t", "79.00", shipping="0"), "GBP")
    assert evaluate(listing, "NM", item, prefs).is_deal  # 21% under
    assert not evaluate(listing, "LP", item, prefs).is_deal  # fair 85, only 7% under
    assert evaluate(listing, "LP", {**item, "priority": 1}, prefs).is_deal
    assert not evaluate(listing, "DMG", {**item, "market_gbp": 500.0}, prefs).is_deal
    low_feedback = parse_item(
        ebay_item("y", "t", "10.00", seller={"username": "new", "feedbackScore": 0}), "GBP"
    )
    assert not evaluate(low_feedback, "NM", {**item, "market_gbp": 13.0}, prefs).is_deal
    too_cheap = parse_item(ebay_item("z", "t", "10.00", shipping="0"), "GBP")
    assert not evaluate(too_cheap, "NM", item, prefs).is_deal  # 90% under: not a real card


def test_ebay_prices_override_a_bad_price_guide():
    """Mew VMAX 269/264: the guide said £173 but copies sell on eBay for about £18."""
    prefs = {
        "min_discount_pct": 20,
        "priority_discount_pct": 5,
        "min_saving_gbp": 2,
        "played_ok_above_gbp": 15,
        "allow_damaged": False,
        "min_seller_feedback": 5,
        "trusted_seller_above_gbp": 30,
        "trusted_seller_feedback": 50,
        "suspicious_above_pct": 50,
    }
    item = {"market_gbp": 173.4, "ebay_median_gbp": 18.0, "target_price": None, "priority": 0}
    listing = parse_item(ebay_item("m", "t", "45.00", shipping="0"), "GBP")
    verdict = evaluate(listing, "NM", item, prefs)
    assert not verdict.is_deal and verdict.fair == 18.0


def test_fakes_far_below_ebay_prices_are_ignored():
    prefs = {
        "min_discount_pct": 20,
        "priority_discount_pct": 5,
        "min_saving_gbp": 2,
        "played_ok_above_gbp": 15,
        "allow_damaged": False,
        "min_seller_feedback": 5,
        "trusted_seller_above_gbp": 30,
        "trusted_seller_feedback": 50,
        "suspicious_above_pct": 50,
    }
    # Delta Charizard: genuine copies around £400 on eBay, a "deal" at £100 is a fake.
    item = {"market_gbp": 488.0, "ebay_median_gbp": 420.0, "target_price": None, "priority": 0}
    listing = parse_item(ebay_item("d", "t", "100.19", shipping="0"), "GBP")
    assert not evaluate(listing, "?", item, prefs).is_deal


def test_old_junk_deals_are_cleared(catalog_db, settings):
    with catalog_db.tx() as c:
        c.execute(
            "INSERT INTO listings (item_id, title, url, price, shipping, total, condition,"
            " first_seen, last_seen, match_item_id, discount_pct, is_deal, alert_status)"
            " VALUES ('junk', 'Charizard 4/102 CREDIT CARD SKIN', 'u', 1, 1, 2, '?',"
            " 'x', strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), 'base1-4:holo', 99, 1, 'sent')"
        )
    _, scanner = run_scan(catalog_db, settings, FakeEbay())
    assert "junk" not in {d["listing_id"] for d in scanner.deals()}


def test_parse_item_unknown_postage_assumed():
    listing = parse_item(ebay_item("z", "t", "5.00", shipping=None), "GBP")
    assert listing.shipping == 2.0 and not listing.shipping_known
    assert (
        parse_item(
            ebay_item("z", "t", "5.00") | {"price": {"value": "5", "currency": "USD"}}, "GBP"
        )
        is None
    )


def test_search_query():
    assert (
        search_query(
            {"pokemon": "Charizard", "local_id": "4", "set_official": 102, "set_name": "Base Set"}
        )
        == "Charizard 4/102"
    )
    assert (
        search_query(
            {
                "pokemon": "Charizard",
                "local_id": "SWSH050",
                "set_official": 0,
                "set_name": "SWSH Black Star Promos",
            }
        )
        == "Charizard SWSH050"
    )


def test_alert_text_without_price_guide():
    from pokehunter.alerts import format_deal

    deal = {
        "card_name": "Mew",
        "set_name": "Promo",
        "local_id": "8",
        "variant": "holo",
        "total": 9.5,
        "fair_gbp": None,
        "discount_pct": None,
        "target_price": 10.0,
        "market_gbp": None,
        "ebay_median_gbp": None,
        "condition": "NM",
        "title": "Ancient Mew promo",
        "url": "https://www.ebay.co.uk/itm/1",
    }
    text = format_deal(deal, None)
    assert "£10.00 alert price" in text and "not enough listings" in text


def test_valuable_cards_need_established_seller():
    from pokehunter.db import DEFAULT_PREFS

    # Charizard V 79/73 gold fake: seller with 25 feedback.
    item = {"market_gbp": 120.0, "ebay_median_gbp": 110.0, "target_price": None, "priority": 0}
    seller = {"username": "new", "feedbackScore": 25}
    listing = parse_item(ebay_item("v", "t", "70.00", shipping="0", seller=seller), "GBP")
    assert not evaluate(listing, "LP", item, DEFAULT_PREFS).is_deal
    trusted = parse_item(ebay_item("w", "t", "70.00", shipping="0"), "GBP")  # 250 feedback
    assert evaluate(trusted, "LP", item, DEFAULT_PREFS).is_deal


def test_uk_only_filter_sent_to_ebay(catalog_db, settings):
    fake = FakeEbay()
    urls = []
    original = fake.__call__

    def spy(request):
        urls.append(str(request.url))
        return original(request)

    run_scan(catalog_db, settings, spy)
    searches = [u for u in urls if "item_summary" in u]
    assert searches and all("itemLocationCountry%3AGB" in u for u in searches)


class StubPhotos:
    """Pretends to be Claude: says the Base Set LP copy is a fake."""

    ready = True

    def __init__(self, verdict="fake"):
        self.verdict = verdict
        self.calls = []

    async def check(self, claimed, title, image_urls):
        from pokehunter.photocheck import PhotoVerdict

        self.calls.append((claimed, title, image_urls))
        return PhotoVerdict(self.verdict, "Whole card is gold metal")


def run_scan_with_photos(db, settings, fake, photos):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
            scanner = Scanner(
                db, settings, EbayClient(settings, http, db), Telegram(settings, http), photos
            )
            return await scanner.scan(), scanner

    return asyncio.run(go())


def test_photo_check_blocks_fakes(catalog_db, settings):
    fake = FakeEbay()
    photos = StubPhotos("fake")
    _, scanner = run_scan_with_photos(catalog_db, settings, fake, photos)
    assert scanner.deals() == [] and fake.messages == []
    claimed, title, urls = photos.calls[0]
    assert claimed == "Charizard from Base Set, number 4/102, Holo"
    assert urls == ["https://i.ebayimg.com/1.jpg"]
    # Checked once only, and stays rejected on later scans.
    run_scan_with_photos(catalog_db, settings, fake, photos)
    assert len(photos.calls) == 1 and scanner.deals() == []


def test_photo_check_genuine_alerts_with_note(catalog_db, settings):
    fake = FakeEbay()
    _, scanner = run_scan_with_photos(catalog_db, settings, fake, StubPhotos("genuine"))
    assert [d["listing_id"] for d in scanner.deals()] == ["1"]
    assert "Photo check: looks genuine" in fake.messages[0]
