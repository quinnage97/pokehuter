from __future__ import annotations

import asyncio
import base64
import json

import httpx

from pokehunter.ebay import EbayApiError, EbayClient, decode_signature_header, parse_listing


def test_parse_listing_includes_shipping_and_aspects():
    listing = parse_listing(
        {
            "itemId": "v1|123|0",
            "title": "English Mew card",
            "itemWebUrl": "https://example.com/item",
            "price": {"value": "10.00", "currency": "GBP"},
            "shippingOptions": [{"shippingCost": {"value": "2.50", "currency": "GBP"}}],
            "buyingOptions": ["FIXED_PRICE"],
            "localizedAspects": [{"name": "Language", "value": "English"}],
        }
    )
    assert listing is not None
    assert str(listing.total_price) == "12.50"
    assert listing.aspects["language"] == ("English",)


def test_oauth_token_is_cached(settings):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": "abc", "expires_in": 7200})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = EbayClient(settings, http)
            assert await client.application_token() == "abc"
            assert await client.application_token() == "abc"

    asyncio.run(run())
    assert calls == 1


def test_decode_signature_header():
    value = base64.b64encode(b'{"kid":"key-1","signature":"c2ln"}').decode()
    assert decode_signature_header(value) == {"kid": "key-1", "signature": "c2ln"}


def test_search_and_public_key_use_oauth_and_cache(settings):
    settings.ebay_delivery_postcode = "SW1A 1AA"
    requested_paths = []
    public_key_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal public_key_calls
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if "/item_summary/search" in request.url.path:
            assert request.headers["x-ebay-c-marketplace-id"] == "EBAY_GB"
            assert request.headers["x-ebay-c-enduserctx"] == (
                "contextualLocation=country%3DGB%2Czip%3DSW1A%201AA"
            )
            return httpx.Response(
                200,
                json={
                    "itemSummaries": [
                        {
                            "itemId": "1",
                            "title": "English Mew Pokemon Card",
                            "itemWebUrl": "https://example.com/1",
                            "price": {"value": "20", "currency": "GBP"},
                            "shippingOptions": [
                                {"shippingCost": {"value": "0", "currency": "GBP"}}
                            ],
                        }
                    ]
                },
            )
        if "/public_key/" in request.url.path:
            public_key_calls += 1
            return httpx.Response(
                200,
                json={"key": "-----BEGIN PUBLIC KEY-----x-----END PUBLIC KEY-----"},
            )
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = EbayClient(settings, http)
            listings = await client.search("Mew")
            assert listings[0].item_id == "1"
            assert await client.get_public_key("key-1") == await client.get_public_key("key-1")

    asyncio.run(run())
    assert public_key_calls == 1
    assert any(path.endswith("/item_summary/search") for path in requested_paths)


def test_decode_signature_rejects_malformed_header():
    try:
        decode_signature_header(base64.b64encode(json.dumps({"kid": "x"}).encode()).decode())
    except ValueError as exc:
        assert "Malformed" in str(exc)
    else:
        raise AssertionError("malformed signature was accepted")


def test_parse_listing_rejects_unknown_or_wrong_currency_shipping():
    common = {
        "itemId": "1",
        "title": "English Mew Pokemon Card",
        "itemWebUrl": "https://example.com/1",
        "price": {"value": "20", "currency": "GBP"},
    }
    assert parse_listing(common) is None
    assert (
        parse_listing(
            {
                **common,
                "shippingOptions": [{"shippingCost": {"value": "5", "currency": "USD"}}],
            }
        )
        is None
    )
    assert (
        parse_listing(
            {
                **common,
                "shippingOptions": [
                    {
                        "shippingServiceCode": "LocalPickup",
                        "shippingCost": {"value": "0", "currency": "GBP"},
                    }
                ],
            }
        )
        is None
    )


def test_oauth_error_hides_response_body(settings):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(401, text="secret body"))
        async with httpx.AsyncClient(transport=transport) as http:
            client = EbayClient(settings, http)
            try:
                await client.application_token()
            except EbayApiError as exc:
                assert "401" in str(exc)
                assert "secret body" not in str(exc)
            else:
                raise AssertionError("OAuth failure was accepted")

    asyncio.run(run())
