from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from conftest import make_listing

from pokehunter.models import Bargain
from pokehunter.telegram import TelegramClient, TelegramError


def test_telegram_alert_escapes_html_and_includes_savings(settings):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"ok": True})

    bargain = Bargain(
        listing=make_listing("1", "50", title="Charizard <script> English"),
        market_price=Decimal("100"),
        estimated_savings=Decimal("50"),
        discount_percent=50,
        score=95,
        comparable_count=5,
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await TelegramClient(settings, http).send_bargain(bargain)

    asyncio.run(run())
    assert "&lt;script&gt;" in captured["text"]
    assert "GBP 50.00" in captured["text"]
    assert "current eBay asking prices" in captured["text"]


def test_telegram_http_failure_raises(settings):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(500))
        bargain = Bargain(
            listing=make_listing("1", "50"),
            market_price=Decimal("100"),
            estimated_savings=Decimal("50"),
            discount_percent=50,
            score=95,
            comparable_count=5,
        )
        async with httpx.AsyncClient(transport=transport) as http:
            try:
                await TelegramClient(settings, http).send_bargain(bargain)
            except TelegramError:
                return
        raise AssertionError("Telegram HTTP failure was accepted")

    asyncio.run(run())


def test_telegram_access_check_is_read_only(settings):
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"ok": True, "result": {}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await TelegramClient(settings, http).check_access()

    asyncio.run(run())
    assert methods == ["getMe", "getChat"]
    assert "sendMessage" not in methods


def test_telegram_network_error_does_not_expose_bot_token(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed at " + str(request.url), request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            try:
                await TelegramClient(settings, http).check_access()
            except TelegramError as exc:
                assert "telegram-token" not in str(exc)
                assert exc.__cause__ is None
                return
        raise AssertionError("Telegram network failure was accepted")

    asyncio.run(run())
