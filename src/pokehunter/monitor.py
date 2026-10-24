from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .config import Settings
from .database import Database
from .discovery import is_target_listing
from .ebay import EbayClient
from .market import score_listing
from .models import Bargain, Listing
from .telegram import TelegramClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunResult:
    discovered: int
    candidates: int
    alerts: int
    errors: int


class Monitor:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        ebay: EbayClient,
        telegram: TelegramClient,
    ) -> None:
        self.settings = settings
        self.database = database
        self.ebay = ebay
        self.telegram = telegram

    async def run(self) -> RunResult:
        started = time.monotonic()
        self.settings.require_ebay_credentials()
        self.settings.require_telegram_credentials()
        self.database.initialize()
        run_id = self.database.start_run()
        discovered = candidates = alerts = errors = 0
        listings_by_query: list[list[Listing]] = []

        try:
            for query in self.settings.search_queries:
                try:
                    found = await self.ebay.search(query)
                    discovered += len(found)
                    eligible = [
                        listing
                        for listing in found
                        if is_target_listing(
                            listing,
                            currency=self.settings.ebay_currency,
                            require_english=self.settings.require_english,
                        )
                    ]
                    listings_by_query.append(eligible)
                    logger.info(
                        "eBay search completed",
                        extra={"event": "search_complete", "query": query, "count": len(found)},
                    )
                except Exception:
                    errors += 1
                    logger.exception(
                        "eBay search failed",
                        extra={"event": "search_failed", "query": query},
                    )

            bargains: list[Bargain] = []
            seen_in_run: set[str] = set()
            for pool in listings_by_query:
                for listing in pool:
                    if listing.item_id in seen_in_run:
                        continue
                    seen_in_run.add(listing.item_id)
                    already_alerted = self.database.observe(listing)
                    bargain = score_listing(
                        listing,
                        pool,
                        min_comparables=self.settings.min_comparables,
                        similarity_threshold=self.settings.title_similarity_threshold,
                    )
                    if not bargain:
                        continue
                    if (
                        bargain.discount_percent < self.settings.min_discount_percent
                        or bargain.estimated_savings < self.settings.min_savings
                        or bargain.score < self.settings.min_bargain_score
                    ):
                        continue
                    candidates += 1
                    if not already_alerted:
                        bargains.append(bargain)

            for bargain in sorted(bargains, key=lambda item: item.score, reverse=True):
                item_id = bargain.listing.item_id
                try:
                    await self.telegram.send_bargain(bargain)
                    self.database.mark_alert_attempt(item_id)
                    self.database.mark_alert_sent(item_id)
                    alerts += 1
                    logger.info(
                        "Telegram bargain alert sent",
                        extra={"event": "alert_sent", "item_id": item_id},
                    )
                except Exception as exc:
                    errors += 1
                    self.database.mark_alert_attempt(item_id, error=type(exc).__name__)
                    logger.exception(
                        "Telegram bargain alert failed",
                        extra={"event": "alert_failed", "item_id": item_id},
                    )
        finally:
            self.database.finish_run(
                run_id,
                discovered=discovered,
                candidates=candidates,
                alerts=alerts,
                errors=errors,
            )
            self.database.prune()
            logger.info(
                "monitor run finished",
                extra={
                    "event": "monitor_complete",
                    "count": alerts,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
        return RunResult(discovered, candidates, alerts, errors)
