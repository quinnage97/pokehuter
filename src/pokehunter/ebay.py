from __future__ import annotations

import base64
import contextlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings
from .db import Database

API = "https://api.ebay.com"


class EbayError(RuntimeError):
    pass


class BudgetExhausted(EbayError):
    pass


@dataclass
class Listing:
    item_id: str
    title: str
    url: str
    image: str | None
    price: float
    shipping: float
    shipping_known: bool
    seller: str | None
    feedback_score: int | None
    feedback_pct: float | None
    listed_at: str | None

    @property
    def total(self) -> float:
        return round(self.price + self.shipping, 2)


class EbayClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, db: Database) -> None:
        self.settings = settings
        self.http = http
        self.db = db
        self._token: str | None = None
        self._token_expiry = 0.0

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry - 120:
            return self._token
        if not self.settings.ebay_ready:
            raise EbayError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET are not set")
        secret = self.settings.ebay_client_secret.get_secret_value()  # type: ignore[union-attr]
        basic = base64.b64encode(f"{self.settings.ebay_client_id}:{secret}".encode()).decode()
        response = await self.http.post(
            f"{API}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=20,
        )
        if response.is_error:
            raise EbayError(
                f"eBay login failed (HTTP {response.status_code}). "
                "Check the Client ID and Client Secret are the *Production* keys."
            )
        body = response.json()
        self._token = body["access_token"]
        self._token_expiry = time.monotonic() + int(body.get("expires_in", 7200))
        return self._token

    async def search(
        self, query: str, *, sort: str | None = None, limit: int = 200
    ) -> list[Listing]:
        if self.db.calls_today() >= self.settings.ebay_daily_call_budget:
            raise BudgetExhausted("daily eBay call budget used up; resuming tomorrow")
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace_id,
            "Accept": "application/json",
        }
        context = []
        if self.settings.ebay_delivery_postcode:
            location = (
                f"country={self.settings.ebay_delivery_country},"
                f"zip={self.settings.ebay_delivery_postcode}"
            )
            context.append(f"contextualLocation={quote(location, safe='')}")
        if self.settings.ebay_affiliate_campaign_id:
            context.append(f"affiliateCampaignId={self.settings.ebay_affiliate_campaign_id}")
        if context:
            headers["X-EBAY-C-ENDUSERCTX"] = ",".join(context)
        params = {
            "q": query,
            "category_ids": self.settings.ebay_category_id,
            "filter": (
                f"buyingOptions:{{FIXED_PRICE}},deliveryCountry:{self.settings.ebay_delivery_country}"
            ),
            "limit": str(limit),
        }
        if sort:
            params["sort"] = sort
        self.db.count_call()
        response = await self.http.get(
            f"{API}/buy/browse/v1/item_summary/search", headers=headers, params=params, timeout=30
        )
        if response.status_code == 401:
            self._token = None
        if response.is_error:
            detail = ""
            with contextlib.suppress(Exception):
                detail = response.json()["errors"][0]["message"]
            raise EbayError(f"eBay search failed (HTTP {response.status_code}) {detail}".strip())
        return [
            listing
            for item in response.json().get("itemSummaries") or []
            if (listing := parse_item(item, self.settings.ebay_currency)) is not None
        ]


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_item(item: dict[str, Any], currency: str) -> Listing | None:
    price = item.get("price") or {}
    value = _float(price.get("value"))
    if not value or price.get("currency") != currency or not item.get("itemId"):
        return None
    # Multi-variation listings ("choose your card") show the cheapest option's price.
    if item.get("itemGroupType"):
        return None
    if "FIXED_PRICE" not in (item.get("buyingOptions") or []):
        return None

    shipping_costs = []
    for option in item.get("shippingOptions") or []:
        cost = option.get("shippingCost") or {}
        amount = _float(cost.get("value"))
        if amount is not None and cost.get("currency", currency) == currency:
            shipping_costs.append(amount)
    seller = item.get("seller") or {}
    image = (item.get("image") or {}).get("imageUrl") or next(
        (i.get("imageUrl") for i in item.get("thumbnailImages") or []), None
    )
    return Listing(
        item_id=str(item["itemId"]),
        title=str(item.get("title", "")),
        url=item.get("itemAffiliateWebUrl") or item.get("itemWebUrl") or "",
        image=image,
        price=value,
        # Unknown postage: assume a typical tracked UK price rather than free.
        shipping=min(shipping_costs) if shipping_costs else 2.0,
        shipping_known=bool(shipping_costs),
        seller=seller.get("username"),
        feedback_score=int(seller["feedbackScore"]) if "feedbackScore" in seller else None,
        feedback_pct=_float(seller.get("feedbackPercentage")),
        listed_at=item.get("itemCreationDate"),
    )
