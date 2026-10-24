from __future__ import annotations

import asyncio
import json

from conftest import make_listing

from pokehunter import cli


class FakeHttp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


def test_check_ebay_reports_access_without_listing_details(settings, monkeypatch, capsys):
    settings.ebay_affiliate_campaign_id = "1234567890"

    class FakeEbay:
        def __init__(self, *_):
            pass

        async def search(self, query):
            if "Charizard" not in query:
                return []
            return [
                make_listing("1", "20"),
                make_listing("2", "100"),
                make_listing("3", "100"),
                make_listing("4", "100"),
            ]

    monkeypatch.setattr(cli.httpx, "AsyncClient", lambda **_: FakeHttp())
    monkeypatch.setattr(cli, "EbayClient", FakeEbay)

    assert asyncio.run(cli.check_ebay_access(settings)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "ok",
        "environment": "production",
        "queries": 2,
        "parsed_listings": 4,
        "eligible_listings": 4,
        "bargain_candidates": 1,
        "affiliate_tracking": True,
    }


def test_check_telegram_reports_access_without_sending(settings, monkeypatch, capsys):
    calls = []

    class FakeTelegram:
        def __init__(self, *_):
            pass

        async def check_access(self):
            calls.append("checked")

    monkeypatch.setattr(cli.httpx, "AsyncClient", lambda **_: FakeHttp())
    monkeypatch.setattr(cli, "TelegramClient", FakeTelegram)

    assert asyncio.run(cli.check_telegram_access(settings)) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}
    assert calls == ["checked"]
