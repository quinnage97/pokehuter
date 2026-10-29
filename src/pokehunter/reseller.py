"""Reseller finds: listings far below market value, with an honest profit estimate.

Listings 70-90% below market are usually fakes, prints, damaged or wrong cards, so this only
considers listings that passed every other filter (junk words, wrong forms, reported sellers,
the photo check) and whose market price is confirmed by other eBay listings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Database
from .matching import CONDITION_FACTOR, variant_label

WINDOW_HOURS = 18


def estimate(row: dict[str, Any], prefs: dict[str, Any]) -> dict[str, Any] | None:
    """Discount and profit for one listing, or None if it isn't a reseller find."""
    guide, typical = row.get("market_gbp"), row.get("ebay_median_gbp")
    if not typical:
        return None  # without other eBay listings we can't trust the market price
    if guide and max(guide, typical) > 2.5 * min(guide, typical):
        return None  # price guide and eBay disagree: one is wrong, so no estimate
    market = min(p for p in (guide, typical) if p)
    discount = (market - row["total"]) / market * 100
    if not prefs["reseller_min_pct"] <= discount <= prefs["reseller_max_pct"]:
        return None
    resale = market * CONDITION_FACTOR.get(row["condition"], 0.8)
    costs = resale * prefs["reseller_fee_pct"] / 100 + prefs["reseller_postage_gbp"]
    return {
        "market_value": round(market, 2),
        "reseller_discount": round(discount, 1),
        "resale_gbp": round(resale, 2),
        "costs_gbp": round(costs, 2),
        "profit_gbp": round(resale - costs - row["total"], 2),
    }


def finds(db: Database, prefs: dict[str, Any]) -> list[dict[str, Any]]:
    cutoff = (datetime.now(UTC) - timedelta(hours=WINDOW_HOURS)).isoformat()
    rows = db.query(
        """
        SELECT l.item_id AS listing_id, l.*, i.variant, i.owned, i.market_gbp AS guide_gbp,
               i.ebay_median_gbp, i.ebay_comps, c.name AS card_name, c.set_name, c.local_id,
               c.set_official, c.pokemon, c.image AS card_image, c.image_alt, c.custom,
               c.image_override
        FROM listings l
        JOIN items i ON i.id = l.match_item_id
        JOIN cards c ON c.id = i.card_id
        WHERE l.dismissed = 0 AND l.sold = 0 AND l.user_verdict IS NULL
          AND l.last_seen >= ?
          AND COALESCE(l.photo_verdict, '') NOT IN ('fake', 'not_a_card', 'wrong_card')
          AND COALESCE(l.warning, '') NOT LIKE '%Likely fake%'
        """,
        (cutoff,),
    )
    out = []
    for row in rows:
        row["market_gbp"] = row.pop("guide_gbp")
        if numbers := estimate(row, prefs):
            out.append({**row, **numbers, "variant_label": variant_label(row["variant"])})
    out.sort(key=lambda r: -r["profit_gbp"])
    return out
