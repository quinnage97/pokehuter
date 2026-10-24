from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import uvicorn

from .config import Settings
from .database import Database
from .ebay import EbayClient
from .logging import configure_logging
from .monitor import Monitor
from .telegram import TelegramClient


async def run_monitor(settings: Settings) -> int:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        monitor = Monitor(
            settings,
            Database(settings.database_path),
            EbayClient(settings, http),
            TelegramClient(settings, http),
        )
        result = await monitor.run()
    return 1 if result.errors else 0


async def check_ebay_access(settings: Settings) -> int:
    """Verify production OAuth and Browse access without sending alerts or storing listings."""
    timeout = httpx.Timeout(30.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            client = EbayClient(settings, http)
            counts = [len(await client.search(query)) for query in settings.search_queries]
        if not sum(counts):
            raise RuntimeError("eBay Browse returned no usable listings for the configured queries")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "environment": settings.ebay_environment,
                    "queries": len(counts),
                    "parsed_listings": sum(counts),
                    "affiliate_tracking": bool(settings.ebay_affiliate_campaign_id),
                },
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1


async def check_telegram_access(settings: Settings) -> int:
    """Verify bot and chat access without sending a message."""
    timeout = httpx.Timeout(15.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            await TelegramClient(settings, http).check_access()
        print('{"status":"ok"}')
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="PokéHunter eBay bargain monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("monitor", help="run one discovery and alert cycle")
    subparsers.add_parser(
        "check-ebay", help="verify OAuth and Browse permissions without sending alerts"
    )
    subparsers.add_parser(
        "check-telegram", help="verify bot and chat access without sending a message"
    )
    serve = subparsers.add_parser("serve", help="serve health and eBay webhook endpoints")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)
    if args.command == "monitor":
        raise SystemExit(asyncio.run(run_monitor(settings)))
    if args.command == "check-ebay":
        raise SystemExit(asyncio.run(check_ebay_access(settings)))
    if args.command == "check-telegram":
        raise SystemExit(asyncio.run(check_telegram_access(settings)))
    uvicorn.run(
        "pokehunter.app:app",
        host=args.host,
        port=args.port,
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
