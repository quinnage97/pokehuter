from __future__ import annotations

import pytest

from pokehunter.config import Settings
from pokehunter.db import Database

# Trimmed-down TCGdex card responses.
TCGDEX_CARDS = {
    "base1-4": {
        "id": "base1-4",
        "localId": "4",
        "name": "Charizard",
        "rarity": "Rare",
        "image": "https://assets.tcgdex.net/en/base/base1/4",
        "set": {"id": "base1", "name": "Base Set", "cardCount": {"official": 102, "total": 102}},
        "variants": {"normal": False, "reverse": False, "holo": True, "firstEdition": True},
        "pricing": {
            "cardmarket": {"trend": 400.0, "avg30": 380.0, "unit": "EUR"},
            "tcgplayer": {
                "unit": "USD",
                "holofoil": {"marketPrice": 520.0},
                "1st-edition-holofoil": {"marketPrice": 9000.0},
            },
        },
    },
    "sv03.5-199": {
        "id": "sv03.5-199",
        "localId": "199",
        "name": "Charizard ex",
        "rarity": "Special illustration rare",
        "image": "https://assets.tcgdex.net/en/sv/sv03.5/199",
        "set": {"id": "sv03.5", "name": "151", "cardCount": {"official": 165, "total": 207}},
        "variants": {"normal": False, "reverse": False, "holo": True, "firstEdition": False},
        "pricing": {"cardmarket": {"trend": 100.0}, "tcgplayer": None},
    },
    "swsh4-25": {
        "id": "swsh4-25",
        "localId": "025",
        "name": "Charizard",
        "rarity": "Rare",
        "image": None,
        "set": {"id": "swsh4", "name": "Vivid Voltage", "cardCount": {"official": 185}},
        "variants": {"normal": False, "reverse": True, "holo": True},
        "pricing": {"cardmarket": {"trend": 2.0, "trend-holo": 3.0}},
    },
    "swshp-SWSH050": {
        "id": "swshp-SWSH050",
        "localId": "SWSH050",
        "name": "Charizard V",
        "image": None,
        "set": {"id": "swshp", "name": "SWSH Black Star Promos", "cardCount": {"official": 0}},
        "variants": {"holo": True},
        "pricing": {"cardmarket": {"trend": 6.0}},
    },
    "swsh8-114": {
        "id": "swsh8-114",
        "localId": "114",
        "name": "Mew V",
        "image": None,
        "set": {"id": "swsh8", "name": "Fusion Strike", "cardCount": {"official": 264}},
        "variants": {"holo": True},
        "pricing": {"cardmarket": {"trend": 3.0}},
    },
    "A1-283": {
        "id": "A1-283",
        "localId": "283",
        "name": "Mew",
        "set": {"id": "A1", "name": "Genetic Apex", "cardCount": {"official": 226}},
        "variants": {"holo": True},
        "pricing": {},
    },
}
TCGDEX_SETS = {
    "base1": {"releaseDate": "1999-01-09", "serie": {"id": "base", "name": "Base"}},
    "sv03.5": {"releaseDate": "2023-09-22", "serie": {"id": "sv", "name": "Scarlet & Violet"}},
    "swsh4": {"releaseDate": "2020-11-13", "serie": {"id": "swsh", "name": "Sword & Shield"}},
    "swshp": {"releaseDate": "2019-11-15", "serie": {"id": "swsh", "name": "Sword & Shield"}},
    "swsh8": {"releaseDate": "2021-11-12", "serie": {"id": "swsh", "name": "Sword & Shield"}},
    "A1": {"releaseDate": "2024-10-30", "serie": {"id": "tcgp", "name": "Pokémon TCG Pocket"}},
}
TCGDEX_CARDS["base1-58"] = {
    "id": "base1-58",
    "localId": "58",
    "name": "Pikachu",
    "rarity": "Common",
    "image": "https://assets.tcgdex.net/en/base/base1/58",
    "set": {"id": "base1", "name": "Base Set", "cardCount": {"official": 102, "total": 102}},
    "variants": {"normal": True},
    "pricing": {"cardmarket": {"trend": 50.0}},
}
BRIEFS = {
    "Pikachu": ["base1-58"],
    "Charizard": ["base1-4", "sv03.5-199", "swsh4-25", "swshp-SWSH050"],
    "Mew": ["swsh8-114", "A1-283", "mewtwo-1"],
}


def tcgdex_handler(request):
    import httpx

    path = request.url.path
    if "frankfurter" in request.url.host:
        return httpx.Response(200, json={"rates": {"EUR": 1.25, "USD": 1.25}})  # €1 = £0.80
    if path.endswith("/cards"):
        name = request.url.params["name"]
        names = {i: TCGDEX_CARDS.get(i, {}).get("name", "Mewtwo") for i in BRIEFS.get(name, [])}
        briefs = [{"id": i, "name": n} for i, n in names.items()]
        return httpx.Response(200, json=briefs)
    if "/cards/" in path:
        card_id = path.rsplit("/", 1)[1]
        return httpx.Response(200, json=TCGDEX_CARDS[card_id])
    if "/sets/" in path:
        return httpx.Response(200, json=TCGDEX_SETS[path.rsplit("/", 1)[1]])
    return httpx.Response(404)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        ebay_client_id="id",
        ebay_client_secret="secret",
        telegram_bot_token="tg-token",
        telegram_chat_id="42",
        database_path=tmp_path / "test.sqlite3",
    )


@pytest.fixture
def db(settings):
    database = Database(settings.database_path)
    yield database
    database.close()


@pytest.fixture
def catalog_db(db, settings):
    import asyncio

    import httpx

    from pokehunter.catalog import refresh_catalog

    async def build():
        async with httpx.AsyncClient(transport=httpx.MockTransport(tcgdex_handler)) as http:
            await refresh_catalog(db, settings, http)

    asyncio.run(build())
    return db


def ebay_item(item_id, title, price, shipping="1.00", **extra):
    item = {
        "itemId": item_id,
        "title": title,
        "price": {"value": str(price), "currency": "GBP"},
        "buyingOptions": ["FIXED_PRICE"],
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{item_id}",
        "image": {"imageUrl": f"https://i.ebayimg.com/{item_id}.jpg"},
        "seller": {"username": "seller", "feedbackScore": 250, "feedbackPercentage": "99.8"},
    }
    if shipping is not None:
        item["shippingOptions"] = [{"shippingCost": {"value": shipping, "currency": "GBP"}}]
    item.update(extra)
    return item
