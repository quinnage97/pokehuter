from __future__ import annotations

import html

import httpx

from .config import Settings
from .models import Bargain


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http

    async def send_bargain(self, bargain: Bargain) -> None:
        token, chat_id = self.settings.require_telegram_credentials()
        listing = bargain.listing
        text = (
            "🔥 <b>PokéHunter bargain</b>\n\n"
            f"<b>{html.escape(listing.title)}</b>\n"
            f"Price delivered: <b>{listing.currency} {listing.total_price:.2f}</b>\n"
            f"Comparable asking price: {listing.currency} {bargain.market_price:.2f}\n"
            f"Estimated saving: <b>{listing.currency} {bargain.estimated_savings:.2f} "
            f"({bargain.discount_percent:.1f}%)</b>\n"
            f"Bargain score: {bargain.score:.1f}/100 "
            f"({bargain.comparable_count} comparable listings)\n\n"
            f'<a href="{html.escape(listing.url, quote=True)}">View listing on eBay</a>\n\n'
            "<i>Estimate uses current eBay asking prices, not completed sales. "
            "Check authenticity and condition before buying.</i>"
        )
        response = await self.http.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
        if response.is_error:
            raise TelegramError(f"Telegram sendMessage failed with HTTP {response.status_code}")
        body = response.json()
        if not body.get("ok"):
            raise TelegramError("Telegram rejected the alert")
