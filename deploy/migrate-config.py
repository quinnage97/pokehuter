#!/usr/bin/python3 -I
"""Build /etc/pokehunter.env from the fixed legacy installation without exposing values."""

from __future__ import annotations

import argparse
import contextlib
import os
import pwd
import re
import shlex
import stat
from pathlib import Path

LEGACY_ENV = Path("/home/commandadmin/pokehunter/.env")
LEGACY_TOKEN = Path("/home/commandadmin/pokehunter/data/ebay_verification_token")
OUTPUT = Path("/etc/pokehunter.env")
MAX_SOURCE_BYTES = 64 * 1024


def allowed_source_owners() -> set[int]:
    owners = {0, os.geteuid()}
    with contextlib.suppress(KeyError):
        owners.add(pwd.getpwnam("commandadmin").pw_uid)
    return owners


def secure_read(path: Path, *, optional: bool = False) -> str:
    try:
        parent_descriptor = os.open(
            path.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except FileNotFoundError:
        if optional:
            return ""
        raise
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            if optional:
                return ""
            raise
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("migration source is not a regular file")
            if metadata.st_uid not in allowed_source_owners():
                raise ValueError("migration source has an unexpected owner")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise ValueError("migration source must have mode 0600")
            if metadata.st_size > MAX_SOURCE_BYTES:
                raise ValueError("migration source is too large")
            with os.fdopen(descriptor, encoding="utf-8", errors="strict") as source:
                descriptor = -1
                contents = source.read(MAX_SOURCE_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    finally:
        os.close(parent_descriptor)
    if len(contents.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("migration source is too large")
    return contents


def parse_env(contents: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in contents.splitlines():
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


def migrated_values(old: dict[str, str], token: str) -> dict[str, str]:
    return {
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
        "EBAY_VERIFICATION_TOKEN": token.strip() or first(old, "EBAY_VERIFICATION_TOKEN"),
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


def write_config(values: dict[str, str]) -> None:
    if os.geteuid() != 0:
        raise PermissionError("configuration write must run as root")
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError("refusing to overwrite existing configuration")
    parent = OUTPUT.parent
    parent_metadata = parent.lstat()
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != 0
        or parent_metadata.st_mode & 0o022
    ):
        raise PermissionError("configuration directory is not root-controlled")

    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            for key, value in values.items():
                output.write(f"{key}={shlex.quote(value)}\n")
            output.flush()
            os.fsync(output.fileno())
            os.fchown(output.fileno(), 0, 0)
            os.fchmod(output.fileno(), 0o600)
        try:
            os.link(temporary, OUTPUT, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError("refusing to overwrite existing configuration") from None
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    try:
        old = parse_env(secure_read(LEGACY_ENV))
        token = secure_read(LEGACY_TOKEN, optional=True).strip()
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"migration_source_error={type(exc).__name__}")
        return 1

    migrated = migrated_values(old, token)
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
    if any("\n" in value or "\r" in value for value in migrated.values()):
        print("refusing multiline configuration value")
        return 1
    try:
        write_config(migrated)
    except (OSError, ValueError) as exc:
        print(f"config_write_error={type(exc).__name__}")
        return 1
    print(f"config_written={OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
