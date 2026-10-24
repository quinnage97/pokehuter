from __future__ import annotations

import argparse
import asyncio

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


def main() -> None:
    parser = argparse.ArgumentParser(description="PokéHunter eBay bargain monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("monitor", help="run one discovery and alert cycle")
    serve = subparsers.add_parser("serve", help="serve health and eBay webhook endpoints")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)
    if args.command == "monitor":
        raise SystemExit(asyncio.run(run_monitor(settings)))
    uvicorn.run("pokehunter.app:app", host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
