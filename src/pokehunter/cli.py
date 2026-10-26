from __future__ import annotations

import argparse
import asyncio
import json
import logging
import tempfile
from pathlib import Path

import httpx
import uvicorn

from .alerts import Telegram
from .catalog import refresh_catalog
from .config import Settings
from .db import Database
from .ebay import EbayClient
from .scanner import Scanner


async def _once(settings: Settings, what: str) -> int:
    # `check` uses a throwaway database so it can run as root without touching live data.
    db = Database(
        Path(tempfile.mkdtemp()) / "check.sqlite3" if what == "check" else settings.database_path
    )
    async with httpx.AsyncClient(headers={"User-Agent": "PokeHunter/1.0"}) as http:
        needs_catalog = what == "scan" and not db.get_kv("catalog_updated_at")
        if what == "refresh" or needs_catalog:
            print(f"Card list: {await refresh_catalog(db, settings, http)} cards")
        if what == "scan":
            scanner = Scanner(
                db, settings, EbayClient(settings, http, db), Telegram(settings, http)
            )
            print(json.dumps(await scanner.scan(), indent=2))
        if what == "check":
            ok = True
            try:
                found = await EbayClient(settings, http, db).search(
                    "Charizard", sort="newlyListed", limit=5
                )
                print(f"eBay OK: {len(found)} listings, e.g. {found[0].title if found else '-'}")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"eBay PROBLEM: {exc}")
            try:
                await Telegram(settings, http).send("✅ PokéHunter test message")
                print("Telegram OK: test message sent")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"Telegram PROBLEM: {exc}")
            return 0 if ok else 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="pokehunter", description="Pokémon card deal catcher")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the website and the 15-minute scanner")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    sub.add_parser("refresh", help="download the card list and prices now")
    sub.add_parser("scan", help="run one eBay scan now and print the result")
    sub.add_parser("check", help="test the eBay keys and send a Telegram test message")
    parser.add_argument(
        "--env-file", help="read settings from this file (e.g. /etc/pokehunter.env)"
    )
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "serve":
        from .web import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, proxy_headers=True)
    else:
        raise SystemExit(asyncio.run(_once(settings, args.command)))


if __name__ == "__main__":
    main()
