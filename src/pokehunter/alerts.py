from __future__ import annotations

import html
from typing import Any

import httpx

from .config import Settings
from .matching import CONDITION_LABEL, VARIANT_LABEL


class AlertError(RuntimeError):
    pass


def format_deal(deal: dict[str, Any], public_url: str | None) -> str:
    star = "⭐ PRIORITY CARD\n" if deal.get("priority") else ""
    variant = VARIANT_LABEL.get(deal["variant"], deal["variant"])
    lines = [
        f"{star}🔥 <b>{html.escape(deal['card_name'])}</b> — {html.escape(deal['set_name'])} "
        f"#{html.escape(deal['local_id'])} ({variant})",
        "",
        f"<b>£{deal['total']:.2f}</b> delivered "
        f"(fair price for this condition ≈ £{deal['fair_gbp']:.2f}, "
        f"<b>{deal['discount_pct']:.0f}% under</b>)",
        f"Condition: {CONDITION_LABEL.get(deal['condition'], deal['condition'])}"
        + (f" · NM market ≈ £{deal['market_gbp']:.2f}" if deal.get("market_gbp") else ""),
    ]
    if deal.get("feedback_score") is not None:
        rating = deal.get("feedback_pct") or "?"
        lines.append(f"Seller: {deal['feedback_score']} feedback ({rating}%)")
    if deal.get("warning"):
        lines.append(f"⚠️ {html.escape(deal['warning'])}")
    link = f'<a href="{html.escape(deal["url"])}">Open on eBay</a>'
    lines += ["", html.escape(deal["title"]), "", link]
    if public_url:
        lines.append(f'<a href="{html.escape(public_url)}">All deals</a>')
    return "\n".join(lines)


class Telegram:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http

    async def send(self, text: str) -> None:
        if not self.settings.telegram_ready:
            raise AlertError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")
        token = self.settings.telegram_bot_token.get_secret_value()  # type: ignore[union-attr]
        try:
            response = await self.http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": self.settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=20,
            )
        except httpx.HTTPError:
            # The bot token is in the URL, so never let the raw exception reach the logs.
            raise AlertError("could not reach Telegram") from None
        if response.is_error:
            raise AlertError(f"Telegram rejected the message (HTTP {response.status_code})")
