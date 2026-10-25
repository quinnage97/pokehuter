#!/usr/bin/python3 -I
"""Load root-owned configuration, drop privileges, and run one approved release check."""

from __future__ import annotations

import os
import pwd
import re
import shlex
import stat
import sys
from pathlib import Path

CONFIG = Path("/etc/pokehunter.env")
RUNTIME_USER = "pokehunter"
MAX_CONFIG_BYTES = 64 * 1024
ALLOWED_ACTIONS = {"check-ebay", "check-telegram"}
ALLOWED_KEYS = {
    "DATABASE_PATH",
    "EBAY_AFFILIATE_CAMPAIGN_ID",
    "EBAY_AFFILIATE_REFERENCE_ID",
    "EBAY_CATEGORY_ID",
    "EBAY_CLIENT_ID",
    "EBAY_CLIENT_SECRET",
    "EBAY_CURRENCY",
    "EBAY_DELETION_ENDPOINT",
    "EBAY_DELIVERY_COUNTRY",
    "EBAY_DELIVERY_POSTCODE",
    "EBAY_ENVIRONMENT",
    "EBAY_MARKETPLACE_ID",
    "EBAY_RESULT_LIMIT",
    "EBAY_SEARCH_QUERIES",
    "EBAY_VERIFICATION_TOKEN",
    "LOG_LEVEL",
    "MIN_BARGAIN_SCORE",
    "MIN_COMPARABLES",
    "MIN_DISCOUNT_PERCENT",
    "MIN_SAVINGS",
    "PUBLIC_KEY_CACHE_SECONDS",
    "REQUIRE_ENGLISH",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TITLE_SIMILARITY_THRESHOLD",
}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_config(path: Path) -> dict[str, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("configuration must be owned by the effective user with mode 0600")
        if metadata.st_size > MAX_CONFIG_BYTES:
            raise ValueError("configuration is too large")
        with os.fdopen(descriptor, encoding="utf-8", errors="strict") as source:
            descriptor = -1
            contents = source.read(MAX_CONFIG_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(contents.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ValueError("configuration is too large")

    environment: dict[str, str] = {}
    for line_number, raw_line in enumerate(contents.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid configuration line {line_number}")
        key, raw_value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key not in ALLOWED_KEYS:
            raise ValueError(f"unsupported configuration key on line {line_number}")
        if key in environment:
            raise ValueError(f"duplicate configuration key on line {line_number}")
        parsed = shlex.split(raw_value, comments=False, posix=True)
        if len(parsed) != 1:
            raise ValueError(f"invalid configuration value on line {line_number}")
        environment[key] = parsed[0]
    return environment


def trusted_binary(release_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{7,40}", release_id):
        raise ValueError("invalid release identifier")
    release_directory = Path("/opt/pokehunter/releases") / release_id
    binary = release_directory / ".venv/bin/pokehunter"
    for path in (
        Path("/opt"),
        Path("/opt/pokehunter"),
        Path("/opt/pokehunter/releases"),
    ):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ValueError(f"release parent is not root-controlled: {path}")
    for path, expected_type in (
        (release_directory, stat.S_ISDIR),
        (binary, stat.S_ISREG),
    ):
        metadata = path.lstat()
        if not expected_type(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ValueError(f"release path is not root-controlled: {path}")
    return binary


def main() -> None:
    if os.geteuid() != 0:
        fail("release check launcher must run as root")
    if len(sys.argv) != 3 or sys.argv[2] not in ALLOWED_ACTIONS:
        fail("usage: run-release-check.py <release-id> check-ebay|check-telegram")
    release_id, action = sys.argv[1:]
    try:
        environment = load_config(CONFIG)
        binary = trusted_binary(release_id)
        account = pwd.getpwnam(RUNTIME_USER)
    except (KeyError, OSError, UnicodeError, ValueError) as exc:
        fail(f"release check validation failed: {exc}")

    if action == "check-ebay":
        environment = {
            key: value
            for key, value in environment.items()
            if (
                (key.startswith("EBAY_") and key != "EBAY_VERIFICATION_TOKEN")
                or key.startswith("MIN_")
                or key in {"LOG_LEVEL", "REQUIRE_ENGLISH", "TITLE_SIMILARITY_THRESHOLD"}
            )
        }
    else:
        environment = {
            key: value
            for key, value in environment.items()
            if key in {"LOG_LEVEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
        }
    environment.update(
        {
            "HOME": account.pw_dir,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    os.chdir("/")
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.execve(binary, [str(binary), action], environment)


if __name__ == "__main__":
    main()
