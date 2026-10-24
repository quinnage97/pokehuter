#!/usr/bin/env python3
from __future__ import annotations

import argparse
import grp
import os
import re
import shlex
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        try:
            tokens = shlex.split(raw_value, comments=False, posix=True)
        except ValueError:
            continue
        if len(tokens) == 1:
            values[key] = tokens[0]
    return values


def first(values: dict[str, str], *keys: str) -> str:
    return next((values[key] for key in keys if values.get(key)), "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely migrate legacy PokéHunter settings without printing values"
    )
    parser.add_argument("--old-env", type=Path, default=Path("/opt/pokehunter/.env"))
    parser.add_argument(
        "--old-token",
        type=Path,
        default=Path("/opt/pokehunter/data/ebay_verification_token"),
    )
    parser.add_argument("--output", type=Path, default=Path("/etc/pokehunter.env"))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    old = read_env(args.old_env)
    token = args.old_token.read_text().strip() if args.old_token.is_file() else ""
    migrated = {
        "EBAY_CLIENT_ID": first(old, "EBAY_CLIENT_ID", "EBAY_APP_ID", "EBAY_CLIENTID"),
        "EBAY_CLIENT_SECRET": first(old, "EBAY_CLIENT_SECRET", "EBAY_CERT_ID", "EBAY_CLIENTSECRET"),
        "EBAY_ENVIRONMENT": "production",
        "EBAY_MARKETPLACE_ID": "EBAY_GB",
        "EBAY_CURRENCY": "GBP",
        "EBAY_DELIVERY_COUNTRY": "GB",
        "EBAY_DELIVERY_POSTCODE": first(old, "EBAY_DELIVERY_POSTCODE"),
        "EBAY_CATEGORY_ID": "183454",
        "EBAY_SEARCH_QUERIES": "Charizard pokemon card,Mew pokemon card",
        "EBAY_AFFILIATE_CAMPAIGN_ID": first(old, "EBAY_AFFILIATE_CAMPAIGN_ID"),
        "EBAY_AFFILIATE_REFERENCE_ID": first(old, "EBAY_AFFILIATE_REFERENCE_ID") or "pokehunter",
        "EBAY_DELETION_ENDPOINT": "https://pokehunter-nq.duckdns.org/ebay/deletion",
        "EBAY_VERIFICATION_TOKEN": token or first(old, "EBAY_VERIFICATION_TOKEN"),
        "TELEGRAM_BOT_TOKEN": first(old, "TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": first(old, "TELEGRAM_CHAT_ID"),
        "DATABASE_PATH": "/var/lib/pokehunter/pokehunter.sqlite3",
        "LOG_LEVEL": "INFO",
        "MIN_DISCOUNT_PERCENT": "20",
        "MIN_SAVINGS": "10",
        "MIN_BARGAIN_SCORE": "60",
        "MIN_COMPARABLES": "3",
        "TITLE_SIMILARITY_THRESHOLD": "0.45",
        "REQUIRE_ENGLISH": "true",
    }
    required = (
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "EBAY_VERIFICATION_TOKEN",
        "EBAY_DELIVERY_POSTCODE",
        "EBAY_AFFILIATE_CAMPAIGN_ID",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    )
    missing = [key for key in required if not migrated[key]]
    print("migration_sources=checked")
    print("missing_keys=" + (",".join(missing) if missing else "none"))
    if not args.write:
        print("config_written=no")
        return 1 if missing else 0
    if os.environ.get("POKEHUNTER_CONFIG_MIGRATION_APPROVED") != "YES":
        print("config migration approval gate is closed")
        return 1
    if missing:
        print("refusing to write incomplete configuration")
        return 1
    if args.output.exists():
        print("refusing to overwrite existing configuration")
        return 1
    for value in migrated.values():
        if "\n" in value or "\r" in value:
            print("refusing multiline configuration value")
            return 1

    try:
        pokehunter_gid = grp.getgrnam("pokehunter").gr_gid
    except KeyError:
        print("missing pokehunter system group")
        return 1
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(args.output, flags, 0o640)
    with os.fdopen(descriptor, "w") as output:
        for key, value in migrated.items():
            output.write(f"{key}={shlex.quote(value)}\n")
    os.chown(args.output, 0, pokehunter_gid)
    os.chmod(args.output, 0o640)
    print(f"config_written={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
