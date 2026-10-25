from __future__ import annotations

import asyncio

from conftest import make_listing

from pokehunter.database import Database
from pokehunter.monitor import Monitor


class FakeEbay:
    def __init__(self, listings):
        self.listings = listings

    async def search(self, query: str):
        return self.listings


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_bargain(self, bargain):
        self.sent.append(bargain)


def test_monitor_alerts_once_for_same_listing(settings):
    settings.ebay_search_queries = "Charizard pokemon card"
    cheap = make_listing("cheap", "50")
    listings = [cheap] + [
        make_listing(str(index), price) for index, price in enumerate(("90", "100", "110", "120"))
    ]
    telegram = FakeTelegram()
    monitor = Monitor(settings, Database(settings.database_path), FakeEbay(listings), telegram)  # type: ignore[arg-type]
    first = asyncio.run(monitor.run())
    second = asyncio.run(monitor.run())
    assert first.alerts == 1
    assert second.alerts == 0
    assert len(telegram.sent) == 1
