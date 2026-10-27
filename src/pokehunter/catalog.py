"""Build the Charizard / Mew master-set checklist and reference prices from TCGdex.

TCGdex (https://tcgdex.dev) is free and needs no key. Each card carries Cardmarket (EUR)
and TCGplayer (USD) prices, which we convert to GBP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from .config import Settings
from .db import Database, now_iso
from .matching import CardRef, word_match

log = logging.getLogger(__name__)

VARIANT_ORDER = ["firstEdition", "holo", "normal", "reverse"]
EXCLUDED_SERIES = {"tcgp"}  # Pokémon TCG Pocket is a phone game, not physical cards

# Which price fields describe which variant.
# The lowest of these is used, so one odd sale can't inflate the price.
CARDMARKET_KEYS = {
    "normal": ("trend", "avg7", "avg30"),
    "holo": ("trend", "avg7", "avg30"),
    "reverse": ("trend-holo", "avg7-holo", "avg30-holo"),
    "firstEdition": (),  # Cardmarket lists 1st Edition as a separate product
}
TCGPLAYER_KEYS = {
    "normal": ("normal", "unlimited", "unlimited-normal"),
    "holo": ("holofoil", "unlimited-holofoil"),
    "reverse": ("reverse-holofoil",),
    "firstEdition": ("1st-edition-holofoil", "1st-edition-normal", "1st-edition"),
}


def card_variants(card: dict[str, Any]) -> list[str]:
    raw = card.get("variants")
    found: set[str] = set()
    if isinstance(raw, dict):
        found = {k for k in VARIANT_ORDER if raw.get(k)}
    for detail in card.get("variants_detailed") or []:
        if not isinstance(detail, dict):
            continue
        if "1st-edition" in (detail.get("stamp") or []):
            found.add("firstEdition")
        elif detail.get("type") in ("normal", "holo", "reverse") and not detail.get("stamp"):
            found.add(detail["type"])
    return [v for v in VARIANT_ORDER if v in found] or ["normal"]


def _first_number(source: dict[str, Any] | None, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = (source or {}).get(key)
        if isinstance(value, dict):
            value = value.get("marketPrice") or value.get("midPrice")
        if isinstance(value, int | float) and value > 0:
            return float(value)
    return None


def _lowest_number(source: dict[str, Any] | None, keys: tuple[str, ...]) -> float | None:
    values = [(source or {}).get(key) for key in keys]
    numbers = [float(v) for v in values if isinstance(v, int | float) and v > 0]
    return min(numbers) if numbers else None


def variant_price_gbp(
    pricing: dict[str, Any] | None, variant: str, fx: dict[str, float], source: str
) -> tuple[float | None, str | None]:
    pricing = pricing or {}
    cm = _lowest_number(pricing.get("cardmarket"), CARDMARKET_KEYS[variant])
    tp = _first_number(pricing.get("tcgplayer"), TCGPLAYER_KEYS[variant])
    options = {
        "cardmarket": cm * fx["EUR"] if cm else None,
        "tcgplayer": tp * fx["USD"] if tp else None,
    }
    if source == "lowest":
        priced = [(value, name) for name, value in options.items() if value]
        if priced:
            value, name = min(priced)
            return round(value, 2), name
        return None, None
    order = [source] + [s for s in options if s != source]
    for name in order:
        if options[name]:
            return round(options[name], 2), name
    return None, None


async def fetch_fx(http: httpx.AsyncClient, settings: Settings) -> dict[str, float]:
    """GBP value of one EUR / one USD."""
    for url in (
        "https://api.frankfurter.dev/v1/latest?base=GBP&symbols=EUR,USD",
        "https://api.frankfurter.app/latest?from=GBP&to=EUR,USD",
    ):
        try:
            response = await http.get(url, timeout=10)
            response.raise_for_status()
            rates = response.json()["rates"]
            return {"EUR": round(1 / rates["EUR"], 4), "USD": round(1 / rates["USD"], 4)}
        except Exception as exc:  # noqa: BLE001 - fall back to configured rates
            log.warning("exchange rate lookup failed (%s): %s", url, exc)
    return {"EUR": settings.fallback_eur_gbp, "USD": settings.fallback_usd_gbp}


async def refresh_catalog(db: Database, settings: Settings, http: httpx.AsyncClient) -> int:
    """Download every card for the configured Pokémon and update the checklist."""
    base = settings.tcgdex_base.rstrip("/")
    fx = await fetch_fx(http, settings)
    db.set_kv("fx", fx)

    briefs: list[tuple[str, dict[str, Any]]] = []
    for pokemon in settings.pokemon_names:
        response = await http.get(f"{base}/cards", params={"name": pokemon}, timeout=30)
        response.raise_for_status()
        for brief in response.json():
            if word_match(pokemon, brief.get("name", "")):
                briefs.append((pokemon, brief))

    semaphore = asyncio.Semaphore(6)
    set_cache: dict[str, dict[str, Any]] = db.get_kv("tcgdex_sets", {}) or {}

    async def get_json(path: str) -> dict[str, Any] | None:
        async with semaphore:
            for attempt in range(3):
                try:
                    response = await http.get(f"{base}/{path}", timeout=30)
                    if response.status_code == 404:
                        return None
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        log.warning("TCGdex %s failed: %s", path, exc)
                        return None
                    await asyncio.sleep(1 + attempt * 2)
        return None

    details = await asyncio.gather(*(get_json(f"cards/{b['id']}") for _, b in briefs))
    missing_sets = sorted({d["set"]["id"] for d in details if d and d.get("set")} - set(set_cache))
    set_details = await asyncio.gather(*(get_json(f"sets/{s}") for s in missing_sets))
    for set_id, info in zip(missing_sets, set_details, strict=True):
        if info:
            set_cache[set_id] = {
                "releaseDate": info.get("releaseDate"),
                "serie": (info.get("serie") or {}).get("id"),
                "serieName": (info.get("serie") or {}).get("name"),
            }
    db.set_kv("tcgdex_sets", set_cache)

    source = db.prefs()["price_source"]
    stored = 0
    timestamp = now_iso()
    with db.tx() as c:
        for (pokemon, _), card in zip(briefs, details, strict=True):
            if not card or not card.get("set"):
                continue
            set_info = set_cache.get(card["set"]["id"], {})
            if set_info.get("serie") in EXCLUDED_SERIES:
                continue
            image = card.get("image")
            c.execute(
                """
                INSERT INTO cards (id, pokemon, name, local_id, set_id, set_name, set_official,
                    release_date, serie_name, rarity, image, pricing_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, local_id=excluded.local_id, set_name=excluded.set_name,
                    set_official=excluded.set_official, release_date=excluded.release_date,
                    serie_name=excluded.serie_name, rarity=excluded.rarity, image=excluded.image,
                    pricing_json=excluded.pricing_json, updated_at=excluded.updated_at
                """,
                (
                    card["id"],
                    pokemon,
                    card.get("name", pokemon),
                    str(card.get("localId", "")),
                    card["set"]["id"],
                    card["set"].get("name", card["set"]["id"]),
                    (card["set"].get("cardCount") or {}).get("official"),
                    set_info.get("releaseDate"),
                    set_info.get("serieName"),
                    card.get("rarity"),
                    image,
                    json.dumps(card.get("pricing")),
                    timestamp,
                ),
            )
            for variant in card_variants(card):
                market, used = variant_price_gbp(card.get("pricing"), variant, fx, source)
                c.execute(
                    """
                    INSERT INTO items (id, card_id, variant, market_gbp, price_source)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        market_gbp=excluded.market_gbp, price_source=excluded.price_source
                    """,
                    (f"{card['id']}:{variant}", card["id"], variant, market, used),
                )
            stored += 1
    db.set_kv("catalog_updated_at", timestamp)
    log.info("catalogue refreshed: %d cards", stored)
    return stored


def reprice(db: Database) -> None:
    """Recalculate GBP prices after the price source preference changes."""
    fx = db.get_kv("fx") or {"EUR": 0.86, "USD": 0.76}
    source = db.prefs()["price_source"]
    rows = db.query(
        "SELECT i.id, i.variant, c.pricing_json FROM items i JOIN cards c ON c.id = i.card_id"
    )
    with db.tx() as c:
        for row in rows:
            pricing = json.loads(row["pricing_json"] or "null")
            market, used = variant_price_gbp(pricing, row["variant"], fx, source)
            c.execute(
                "UPDATE items SET market_gbp=?, price_source=? WHERE id=?",
                (market, used, row["id"]),
            )


def load_card_refs(db: Database) -> list[CardRef]:
    rows = db.query(
        """
        SELECT c.id, c.pokemon, c.name, c.local_id, c.set_name, c.set_official,
               i.variant, i.market_gbp AS value
        FROM cards c JOIN items i ON i.card_id = c.id
        """
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(row["id"], {**row, "variants": {}})
        entry["variants"][row["variant"]] = row["value"]
    return [
        CardRef(
            id=e["id"],
            pokemon=e["pokemon"],
            name=e["name"],
            local_id=e["local_id"],
            set_name=e["set_name"],
            set_official=e["set_official"],
            variants=e["variants"],
        )
        for e in grouped.values()
    ]
