from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings
from .models import Listing


class EbayApiError(RuntimeError):
    pass


@dataclass(slots=True)
class _AccessToken:
    value: str
    expires_at: float


class EbayClient:
    """Small eBay REST client with application-token and public-key caching."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self._token: _AccessToken | None = None
        self._token_lock = asyncio.Lock()
        self._public_keys: dict[str, tuple[str, float]] = {}

    async def application_token(self) -> str:
        now = time.monotonic()
        if self._token and self._token.expires_at > now + 60:
            return self._token.value
        async with self._token_lock:
            now = time.monotonic()
            if self._token and self._token.expires_at > now + 60:
                return self._token.value
            client_id, client_secret = self.settings.require_ebay_credentials()
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            response = await self.http.post(
                f"{self.settings.ebay_api_base}/identity/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
            )
            if response.is_error:
                raise EbayApiError(f"eBay OAuth failed with HTTP {response.status_code}")
            body = response.json()
            value = body.get("access_token")
            if not value:
                raise EbayApiError("eBay OAuth response did not contain an access token")
            expires_in = int(body.get("expires_in", 7200))
            self._token = _AccessToken(value=value, expires_at=now + expires_in)
            return value

    async def search(self, query: str) -> list[Listing]:
        token = await self.application_token()
        self.settings.validate_browse_config()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace_id,
            "Accept": "application/json",
        }
        contexts: list[str] = []
        if self.settings.ebay_affiliate_campaign_id:
            contexts.append(f"affiliateCampaignId={self.settings.ebay_affiliate_campaign_id}")
            if self.settings.ebay_affiliate_reference_id:
                contexts.append(
                    "affiliateReferenceId="
                    f"{quote(self.settings.ebay_affiliate_reference_id, safe='')}"
                )
        if self.settings.ebay_delivery_postcode:
            location = (
                f"country={self.settings.ebay_delivery_country},"
                f"zip={self.settings.ebay_delivery_postcode}"
            )
            contexts.append(f"contextualLocation={quote(location, safe='')}")
        if contexts:
            headers["X-EBAY-C-ENDUSERCTX"] = ",".join(contexts)
        filters = [
            f"deliveryCountry:{self.settings.ebay_delivery_country}",
            "buyingOptions:{FIXED_PRICE}",
        ]
        if self.settings.ebay_delivery_postcode:
            filters.append(f"deliveryPostalCode:{self.settings.ebay_delivery_postcode}")
        response = await self.http.get(
            f"{self.settings.ebay_api_base}/buy/browse/v1/item_summary/search",
            headers=headers,
            params={
                "q": query,
                "category_ids": self.settings.ebay_category_id,
                "filter": ",".join(filters),
                "limit": str(self.settings.ebay_result_limit),
            },
        )
        if response.is_error:
            raise EbayApiError(f"eBay Browse search failed with HTTP {response.status_code}")
        items = response.json().get("itemSummaries", [])
        return [listing for item in items if (listing := parse_listing(item)) is not None]

    async def get_public_key(self, key_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._~-]{1,200}", key_id):
            raise EbayApiError("Invalid eBay public key identifier")
        now = time.monotonic()
        cached = self._public_keys.get(key_id)
        if cached and cached[1] > now:
            return cached[0]
        token = await self.application_token()
        response = await self.http.get(
            f"{self.settings.ebay_api_base}/commerce/notification/v1/public_key/"
            f"{quote(key_id, safe='')}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.is_error:
            raise EbayApiError(f"eBay public-key retrieval failed with HTTP {response.status_code}")
        key = response.json().get("key")
        if not isinstance(key, str) or "PUBLIC KEY" not in key:
            raise EbayApiError("eBay returned an invalid public key")
        self._public_keys[key_id] = (
            key,
            now + self.settings.public_key_cache_seconds,
        )
        return key


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def parse_listing(item: dict[str, Any]) -> Listing | None:
    price = item.get("price") or {}
    item_price = _money(price.get("value"))
    currency = price.get("currency")
    item_id = item.get("itemId")
    title = item.get("title")
    url = item.get("itemAffiliateWebUrl") or item.get("itemWebUrl")
    if not all((item_id, title, url, currency)) or item_price is None:
        return None

    shipping_costs: list[Decimal] = []
    shipping_options = item.get("shippingOptions") or []
    for option in shipping_options:
        service = " ".join(
            str(option.get(key, "")) for key in ("shippingServiceCode", "shippingServiceName")
        ).casefold()
        if "pickup" in service or "pick up" in service:
            continue
        shipping_money = option.get("shippingCost") or {}
        if shipping_money.get("currency") not in (None, currency):
            continue
        shipping_cost = shipping_money.get("value")
        parsed_shipping = _money(shipping_cost)
        if parsed_shipping is not None and parsed_shipping >= 0:
            shipping_costs.append(parsed_shipping)
    # A missing delivery cost must not be treated as free shipping: that would understate the
    # delivered price and create false bargain alerts.
    if not shipping_costs:
        return None
    shipping = min(shipping_costs)

    aspects: dict[str, tuple[str, ...]] = {}
    for aspect in item.get("localizedAspects") or []:
        name = str(aspect.get("name", "")).strip().casefold()
        value = str(aspect.get("value", "")).strip()
        if name and value:
            aspects[name] = (*aspects.get(name, ()), value)

    return Listing(
        item_id=str(item_id),
        title=str(title),
        url=str(url),
        item_price=item_price,
        shipping_price=shipping,
        currency=str(currency),
        buying_options=tuple(str(option) for option in item.get("buyingOptions") or ()),
        condition=item.get("condition"),
        aspects=aspects,
    )


def decode_signature_header(value: str) -> dict[str, str]:
    try:
        decoded = base64.b64decode(value, validate=True)
        parsed = json.loads(decoded)
        if not isinstance(parsed, dict):
            raise ValueError
        key_id, signature = parsed["kid"], parsed["signature"]
        if not isinstance(key_id, str) or not isinstance(signature, str):
            raise ValueError
        return {"kid": key_id, "signature": signature}
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed X-EBAY-SIGNATURE header") from exc
