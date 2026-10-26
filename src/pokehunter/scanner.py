from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .alerts import AlertError, Telegram, format_deal
from .catalog import load_card_refs
from .config import Settings
from .db import Database, now_iso
from .ebay import BudgetExhausted, EbayClient, EbayError, Listing
from .matching import CONDITION_FACTOR, match_title

log = logging.getLogger(__name__)
DEAL_WINDOW_HOURS = 36  # how long a listing stays on the site after we last saw it


@dataclass
class Verdict:
    is_deal: bool
    fair: float | None
    discount: float | None
    warning: str | None


def evaluate(
    listing: Listing, condition: str, item: dict[str, Any], prefs: dict[str, Any]
) -> Verdict:
    market = item.get("market_gbp")
    target = item.get("target_price")
    fair = round(market * CONDITION_FACTOR[condition], 2) if market else None
    discount = round((fair - listing.total) / fair * 100, 1) if fair else None

    value = market or target or 0
    if condition == "DMG":
        condition_ok = prefs["allow_damaged"]
    elif condition in ("MP", "HP"):
        condition_ok = value >= prefs["played_ok_above_gbp"]
    else:
        condition_ok = True
    seller_ok = (
        listing.feedback_score is None or listing.feedback_score >= prefs["min_seller_feedback"]
    )
    priority = item.get("priority")
    threshold = prefs["priority_discount_pct" if priority else "min_discount_pct"]
    by_market = (
        fair is not None
        and discount is not None
        and discount >= threshold
        and fair - listing.total >= prefs["min_saving_gbp"]
    )
    by_target = target is not None and listing.total <= target

    warnings = []
    if discount is not None and discount >= 60:
        warnings.append("Much cheaper than usual: check the photos and seller carefully")
    if not listing.shipping_known:
        warnings.append("Postage not listed (assumed £2)")
    return Verdict(
        is_deal=bool(condition_ok and seller_ok and (by_market or by_target)),
        fair=fair,
        discount=discount,
        warning="; ".join(warnings) or None,
    )


def search_query(card: dict[str, Any]) -> str:
    if card["set_official"] and "promo" not in card["set_name"].lower():
        return f"{card['pokemon']} {card['local_id']}/{card['set_official']}"
    return f"{card['pokemon']} {card['local_id']}"


class Scanner:
    def __init__(
        self, db: Database, settings: Settings, ebay: EbayClient, telegram: Telegram
    ) -> None:
        self.db = db
        self.settings = settings
        self.ebay = ebay
        self.telegram = telegram

    def pick_targets(self, count: int) -> list[dict[str, Any]]:
        """Cards to search individually this round: starred first, then least recently."""
        rows = self.db.query(
            """
            SELECT c.id, c.pokemon, c.local_id, c.set_name, c.set_official,
                   MAX(i.priority) AS priority, MIN(COALESCE(i.last_searched_at, '')) AS searched
            FROM items i JOIN cards c ON c.id = i.card_id
            WHERE i.owned = 0 AND i.hidden = 0
            GROUP BY c.id
            ORDER BY priority DESC, searched ASC, COALESCE(MAX(i.market_gbp), 0) DESC
            LIMIT ?
            """,
            (count,),
        )
        return rows

    async def scan(self) -> dict[str, Any]:
        prefs = self.db.prefs()
        started = now_iso()
        with self.db.tx() as c:
            run_id = c.execute(
                "INSERT INTO runs(kind, started_at) VALUES('scan', ?)", (started,)
            ).lastrowid
        cards = load_card_refs(self.db)
        items = {row["id"]: row for row in self.db.query("SELECT * FROM items")}
        errors: list[str] = []
        calls = 0
        found: dict[str, Listing] = {}

        queries: list[tuple[str, str | None, int, str | None]] = [
            (name, "newlyListed", 200, None) for name in self.settings.pokemon_names
        ]
        for card in self.pick_targets(int(prefs["targeted_per_scan"])):
            queries.append((search_query(card), "price", 100, card["id"]))

        for query, sort, limit, card_id in queries:
            try:
                results = await self.ebay.search(query, sort=sort, limit=limit)
            except BudgetExhausted as exc:
                errors.append(str(exc))
                break
            except EbayError as exc:
                errors.append(str(exc))
                if calls == 0:
                    break  # the very first call failed: credentials/network, not one query
                continue
            except Exception as exc:  # noqa: BLE001 - network blips must not kill the loop
                errors.append(f"{query}: {type(exc).__name__}")
                continue
            calls += 1
            for listing in results:
                found[listing.item_id] = listing
            if card_id:
                with self.db.tx() as c:
                    c.execute(
                        "UPDATE items SET last_searched_at=? WHERE card_id=?", (now_iso(), card_id)
                    )

        new_deals: list[dict[str, Any]] = []
        timestamp = now_iso()
        for listing in found.values():
            match, _ = match_title(listing.title, cards)
            if not match:
                continue
            item_id = f"{match.card.id}:{match.variant}"
            item = items.get(item_id)
            if not item:
                continue
            verdict = evaluate(listing, match.condition, item, prefs)
            warning = "; ".join(w for w in (match.note, verdict.warning) if w) or None
            with self.db.tx() as c:
                previous = c.execute(
                    "SELECT alert_status, dismissed FROM listings WHERE item_id=?",
                    (listing.item_id,),
                ).fetchone()
                c.execute(
                    """
                    INSERT INTO listings (item_id, title, url, image, price, shipping,
                        shipping_known, total, condition, seller, feedback_score, feedback_pct,
                        listed_at, first_seen, last_seen, match_item_id, market_gbp, fair_gbp,
                        discount_pct, is_deal, warning)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        title=excluded.title, url=excluded.url, image=excluded.image,
                        price=excluded.price, shipping=excluded.shipping,
                        shipping_known=excluded.shipping_known, total=excluded.total,
                        condition=excluded.condition, feedback_score=excluded.feedback_score,
                        feedback_pct=excluded.feedback_pct, last_seen=excluded.last_seen,
                        match_item_id=excluded.match_item_id, market_gbp=excluded.market_gbp,
                        fair_gbp=excluded.fair_gbp, discount_pct=excluded.discount_pct,
                        is_deal=excluded.is_deal, warning=excluded.warning
                    """,
                    (
                        listing.item_id,
                        listing.title,
                        listing.url,
                        listing.image,
                        listing.price,
                        listing.shipping,
                        int(listing.shipping_known),
                        listing.total,
                        match.condition,
                        listing.seller,
                        listing.feedback_score,
                        listing.feedback_pct,
                        listing.listed_at,
                        timestamp,
                        timestamp,
                        item_id,
                        item.get("market_gbp"),
                        verdict.fair,
                        verdict.discount,
                        int(verdict.is_deal),
                        warning,
                    ),
                )
            wanted = not item["owned"] and not item["hidden"]
            fresh = previous is None or not (previous["alert_status"] or previous["dismissed"])
            if verdict.is_deal and wanted and fresh:
                new_deals.append(
                    {
                        "item_id": listing.item_id,
                        "priority": item["priority"],
                        "discount": verdict.discount or 0,
                    }
                )

        alerts = await self._send_alerts(new_deals, prefs, errors)
        deal_count = len(self.deals())
        with self.db.tx() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, ebay_calls=?, listings_seen=?, deals=?, alerts=?,"
                " error=? WHERE id=?",
                (
                    now_iso(),
                    calls,
                    len(found),
                    deal_count,
                    alerts,
                    " | ".join(dict.fromkeys(errors)) or None,
                    run_id,
                ),
            )
        log.info(
            "scan done: %d calls, %d listings, %d deals, %d alerts, %d errors",
            calls,
            len(found),
            deal_count,
            alerts,
            len(errors),
        )
        return {
            "calls": calls,
            "listings": len(found),
            "deals": deal_count,
            "alerts": alerts,
            "errors": errors,
        }

    async def _send_alerts(
        self, new_deals: list[dict[str, Any]], prefs: dict[str, Any], errors: list[str]
    ) -> int:
        new_deals.sort(key=lambda d: (-d["priority"], -d["discount"]))
        sent = 0
        by_id = {d["listing_id"]: d for d in self.deals()}
        for index, deal in enumerate(new_deals):
            status = None
            if not prefs["alerts_enabled"] or not self.settings.telegram_ready:
                status = "off"
            elif index >= prefs["max_alerts_per_scan"]:
                status = "capped"  # still shown on the website
            elif deal["item_id"] in by_id:
                try:
                    await self.telegram.send(
                        format_deal(by_id[deal["item_id"]], self.settings.public_url)
                    )
                    status = "sent"
                    sent += 1
                except AlertError as exc:
                    errors.append(str(exc))
                    continue  # leave unmarked so it retries next round
            with self.db.tx() as c:
                c.execute(
                    "UPDATE listings SET alert_status=?, alerted_at=? WHERE item_id=?",
                    (status, now_iso(), deal["item_id"]),
                )
        return sent

    def deals(self) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(hours=DEAL_WINDOW_HOURS)).isoformat()
        return self.db.query(
            """
            SELECT l.item_id AS listing_id, l.*, i.variant, i.priority, i.target_price,
                   c.name AS card_name, c.set_name, c.local_id, c.image AS card_image, c.pokemon
            FROM listings l
            JOIN items i ON i.id = l.match_item_id
            JOIN cards c ON c.id = i.card_id
            WHERE l.is_deal = 1 AND l.dismissed = 0 AND i.owned = 0 AND i.hidden = 0
              AND l.last_seen >= ?
            ORDER BY i.priority DESC, l.discount_pct DESC
            """,
            (cutoff,),
        )

    def priority_watch(self) -> list[dict[str, Any]]:
        """Cheapest recent listing for every starred card, deal or not."""
        cutoff = (datetime.now(UTC) - timedelta(hours=DEAL_WINDOW_HOURS)).isoformat()
        return self.db.query(
            """
            SELECT i.id AS item, i.variant, i.market_gbp, i.target_price,
                   c.name AS card_name, c.set_name, c.local_id, c.image AS card_image,
                   l.item_id AS listing_id, l.title, l.url, l.total, l.condition,
                   l.fair_gbp, l.discount_pct, MIN(l.total) AS cheapest
            FROM items i JOIN cards c ON c.id = i.card_id
            LEFT JOIN listings l ON l.match_item_id = i.id AND l.last_seen >= ?
                 AND l.dismissed = 0
            WHERE i.priority = 1 AND i.owned = 0
            GROUP BY i.id
            ORDER BY c.release_date, c.local_id
            """,
            (cutoff,),
        )
