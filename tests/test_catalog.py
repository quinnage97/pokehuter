from __future__ import annotations

from pokehunter.catalog import card_variants, load_card_refs, reprice


def test_catalog_builds_variant_checklist(catalog_db):
    items = {r["id"]: r for r in catalog_db.query("SELECT * FROM items")}
    # Pocket (phone game) cards and Mewtwo are left out.
    assert not any(i.startswith("A1-") or "mewtwo" in i for i in items)
    assert set(items) >= {"base1-4:firstEdition", "base1-4:holo", "swsh4-25:reverse"}
    # Lowest price wins: Cardmarket avg30 €380 at €1 = £0.80, below TCGplayer's $520.
    assert items["base1-4:holo"]["market_gbp"] == 304.0
    assert items["base1-4:holo"]["price_source"] == "cardmarket"
    assert items["swsh4-25:reverse"]["market_gbp"] == 2.4
    # 1st Edition only has a TCGplayer price ($9000 at $1 = £0.80).
    assert items["base1-4:firstEdition"]["market_gbp"] == 7200.0
    assert items["base1-4:firstEdition"]["price_source"] == "tcgplayer"
    card = catalog_db.one("SELECT * FROM cards WHERE id='base1-4'")
    assert card["release_date"] == "1999-01-09" and card["set_official"] == 102


def test_reprice_switches_source(catalog_db):
    catalog_db.update_prefs({"price_source": "tcgplayer"})
    reprice(catalog_db)
    item = catalog_db.one("SELECT * FROM items WHERE id='base1-4:holo'")
    assert item["market_gbp"] == 416.0 and item["price_source"] == "tcgplayer"


def test_card_refs(catalog_db):
    refs = {r.id: r for r in load_card_refs(catalog_db)}
    assert refs["base1-4"].variants == {"firstEdition": 7200.0, "holo": 304.0}


def test_variants_fallback():
    assert card_variants({}) == ["normal"]
    detailed = {"variants_detailed": [{"type": "holo"}, {"type": "holo", "stamp": ["1st-edition"]}]}
    assert card_variants(detailed) == ["firstEdition", "holo"]
