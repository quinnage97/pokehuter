from __future__ import annotations

import logging
import statistics
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
    """Is this listing a deal? Prices are compared with the *lower* of the price guide and
    what other eBay sellers are asking for the same card, so a bad guide price can't
    create a fake bargain."""
    guide = item.get("market_gbp")
    ebay_typical = item.get("ebay_median_gbp")
    target = item.get("target_price")
    candidates = [p for p in (guide, ebay_typical) if p]
    base = min(candidates) if candidates else None
    fair = round(base * CONDITION_FACTOR[condition], 2) if base else None
    discount = round((fair - listing.total) / fair * 100, 1) if fair else None

    value = base or target or 0
    if condition == "DMG":
        condition_ok = prefs["allow_damaged"]
    elif condition in ("MP", "HP"):
        condition_ok = value >= prefs["played_ok_above_gbp"]
    else:
        condition_ok = True
    # Valuable cards need an established seller: new accounts are where most fakes come from.
    needed = prefs["min_seller_feedback"]
    if (base or target or 0) >= prefs["trusted_seller_above_gbp"]:
        needed = max(needed, prefs["trusted_seller_feedback"])
    seller_ok = listing.feedback_score is None or listing.feedback_score >= needed
    priority = item.get("priority")
    threshold = prefs["priority_discount_pct" if priority else "min_discount_pct"]
    by_market = (
        fair is not None
        and discount is not None
        and discount >= threshold
        and fair - listing.total >= prefs["min_saving_gbp"]
    )
    by_target = target is not None and listing.total <= target
    # Fakes are priced far below every genuine copy. Without other eBay listings to compare
    # with, be stricter still.
    limit = prefs["suspicious_above_pct"] if ebay_typical else prefs["suspicious_above_pct"] * 0.7
    suspicious = discount is not None and discount > limit

    warnings = []
    if not ebay_typical:
        warnings.append("Not enough other eBay listings to double-check the price")
    if not listing.shipping_known:
        warnings.append("Postage not listed (assumed £2)")
    return Verdict(
        is_deal=bool(condition_ok and seller_ok and not suspicious and (by_market or by_target)),
        fair=fair,
        discount=discount,
        warning="; ".join(warnings) or None,
    )


def ebay_medians(db: Database, min_comps: int = 4) -> dict[str, tuple[float, int]]:
    """Typical asking price (median, delivered) of each card version on eBay this week,
    from unplayed / unstated-condition copies."""
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    rows = db.query(
        "SELECT match_item_id, total FROM listings WHERE last_seen >= ? AND dismissed = 0"
        " AND condition IN ('NM', 'LP', '?')",
        (cutoff,),
    )
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(row["match_item_id"], []).append(row["total"])
    return {
        item: (round(statistics.median(totals), 2), len(totals))
        for item, totals in grouped.items()
        if len(totals) >= min_comps
    }


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

    async def _run(self, queries, found, errors, stats) -> None:
        for query, sort, limit, card_id in queries:
            if stats.get("stop"):
                return
            try:
                results = await self.ebay.search(
                    query, sort=sort, limit=limit, uk_only=self.db.prefs()["uk_sellers_only"]
                )
            except BudgetExhausted as exc:
                errors.append(str(exc))
                stats["stop"] = True
                return
            except EbayError as exc:
                errors.append(str(exc))
                if stats["calls"] == 0:
                    stats["stop"] = True  # first call failed: credentials/network
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - network blips must not kill the loop
                errors.append(f"{query}: {type(exc).__name__}")
                continue
            stats["calls"] += 1
            for listing in results:
                found[listing.item_id] = listing
            if card_id:
                with self.db.tx() as c:
                    c.execute(
                        "UPDATE items SET last_searched_at=? WHERE card_id=?", (now_iso(), card_id)
                    )

    async def scan(self) -> dict[str, Any]:
        prefs = self.db.prefs()
        with self.db.tx() as c:
            run_id = c.execute(
                "INSERT INTO runs(kind, started_at) VALUES('scan', ?)", (now_iso(),)
            ).lastrowid
        cards = load_card_refs(self.db)
        cards_by_id = {card.id: card for card in cards}
        items = {row["id"]: row for row in self.db.query("SELECT * FROM items")}
        errors: list[str] = []
        stats: dict[str, Any] = {"calls": 0}
        found: dict[str, Listing] = {}

        # 1. Newly listed cards.
        sweeps = [(name, "newlyListed", 200, None) for name in self.settings.pokemon_names]
        await self._run(sweeps, found, errors, stats)

        # 2. Card-by-card searches. Cards with a possible deal go first, so the price can be
        #    checked against everything else listed for that card before anyone is alerted.
        verify: list[str] = []
        for listing in found.values():
            match, _ = match_title(listing.title, cards)
            if not match or match.card.id in verify:
                continue
            item = items.get(f"{match.card.id}:{match.variant}")
            if not item or item["owned"] or item["hidden"]:
                continue
            unchecked = {**prefs, "suspicious_above_pct": 1000}
            if evaluate(listing, match.condition, item, unchecked).is_deal:
                verify.append(match.card.id)
        verify = verify[:10]
        budget = int(prefs["targeted_per_scan"]) + len(verify)
        targets: list[dict[str, Any]] = [
            {
                "id": cid,
                "pokemon": cards_by_id[cid].pokemon,
                "local_id": cards_by_id[cid].local_id,
                "set_name": cards_by_id[cid].set_name,
                "set_official": cards_by_id[cid].set_official,
            }
            for cid in verify
        ]
        for card in self.pick_targets(budget):
            if len(targets) >= budget:
                break
            if card["id"] not in verify:
                targets.append(card)
        await self._run(
            [(search_query(card), "price", 100, card["id"]) for card in targets],
            found,
            errors,
            stats,
        )

        # 3. Store every recognised listing, then work out typical eBay prices.
        timestamp = now_iso()
        seen_now: set[str] = set()
        with self.db.tx() as c:
            for listing in found.values():
                match, _ = match_title(listing.title, cards)
                if not match:
                    continue
                item_id = f"{match.card.id}:{match.variant}"
                if item_id not in items:
                    continue
                seen_now.add(listing.item_id)
                c.execute(
                    """
                    INSERT INTO listings (item_id, title, url, image, price, shipping,
                        shipping_known, total, condition, seller, feedback_score, feedback_pct,
                        listed_at, first_seen, last_seen, match_item_id, warning)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        title=excluded.title, url=excluded.url, image=excluded.image,
                        price=excluded.price, shipping=excluded.shipping,
                        shipping_known=excluded.shipping_known, total=excluded.total,
                        condition=excluded.condition, feedback_score=excluded.feedback_score,
                        feedback_pct=excluded.feedback_pct, last_seen=excluded.last_seen,
                        match_item_id=excluded.match_item_id, warning=excluded.warning
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
                        match.note or None,
                    ),
                )
        medians = ebay_medians(self.db)
        with self.db.tx() as c:
            c.execute("UPDATE items SET ebay_median_gbp = NULL, ebay_comps = NULL")
            c.executemany(
                "UPDATE items SET ebay_median_gbp = ?, ebay_comps = ? WHERE id = ?",
                [(median, count, item_id) for item_id, (median, count) in medians.items()],
            )
        items = {row["id"]: row for row in self.db.query("SELECT * FROM items")}

        # 4. Judge every recent listing with today's prices and rules.
        new_deals = self.reevaluate(cards, items, prefs, seen_now)
        alerts = await self._send_alerts(new_deals, prefs, errors)
        deal_count = len(self.deals())
        with self.db.tx() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, ebay_calls=?, listings_seen=?, deals=?, alerts=?,"
                " error=? WHERE id=?",
                (
                    now_iso(),
                    stats["calls"],
                    len(found),
                    deal_count,
                    alerts,
                    " | ".join(dict.fromkeys(errors)) or None,
                    run_id,
                ),
            )
        log.info(
            "scan done: %d calls, %d listings, %d deals, %d alerts, %d errors",
            stats["calls"],
            len(found),
            deal_count,
            alerts,
            len(errors),
        )
        return {
            "calls": stats["calls"],
            "listings": len(found),
            "deals": deal_count,
            "alerts": alerts,
            "errors": errors,
        }

    def reevaluate(
        self,
        cards: list,
        items: dict[str, dict[str, Any]],
        prefs: dict[str, Any],
        seen_now: set[str],
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(hours=DEAL_WINDOW_HOURS)).isoformat()
        rows = self.db.query("SELECT * FROM listings WHERE last_seen >= ?", (cutoff,))
        updates, new_deals = [], []
        for row in rows:
            item = items.get(row["match_item_id"])
            if not item:
                continue
            listing = Listing(
                item_id=row["item_id"],
                title=row["title"],
                url=row["url"],
                image=row["image"],
                price=row["price"],
                shipping=row["shipping"],
                shipping_known=bool(row["shipping_known"]),
                seller=row["seller"],
                feedback_score=row["feedback_score"],
                feedback_pct=row["feedback_pct"],
                listed_at=row["listed_at"],
            )
            match, _ = match_title(row["title"], cards)
            verdict = evaluate(listing, row["condition"], item, prefs)
            is_deal = bool(match) and verdict.is_deal
            notes = [match.note] if match and match.note else []
            if verdict.warning:
                notes.append(verdict.warning)
            updates.append(
                (
                    item.get("market_gbp"),
                    verdict.fair,
                    verdict.discount,
                    int(is_deal),
                    "; ".join(notes) or None,
                    row["item_id"],
                )
            )
            wanted = not item["owned"] and not item["hidden"]
            fresh = not row["alert_status"] and not row["dismissed"]
            if is_deal and wanted and fresh and row["item_id"] in seen_now:
                new_deals.append(
                    {
                        "item_id": row["item_id"],
                        "priority": item["priority"],
                        "discount": verdict.discount or 0,
                    }
                )
        with self.db.tx() as c:
            c.executemany(
                "UPDATE listings SET market_gbp=?, fair_gbp=?, discount_pct=?, is_deal=?,"
                " warning=? WHERE item_id=?",
                updates,
            )
        return new_deals

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
                   i.ebay_median_gbp, i.ebay_comps,
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
