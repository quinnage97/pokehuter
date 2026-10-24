#!/usr/bin/python3 -I
"""Read-only eBay OAuth/Browse probe for the legacy server configuration."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import stat
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_ENV_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


DIRECT_OPENER = build_opener(ProxyHandler({}), NoRedirectHandler())


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("legacy environment is not a regular file")
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError(
                "legacy environment must be owned by the effective user with mode 0600"
            )
        if metadata.st_size > MAX_ENV_BYTES:
            raise ValueError("legacy environment is too large")
        with os.fdopen(descriptor, encoding="utf-8", errors="strict") as source:
            descriptor = -1
            contents = source.read(MAX_ENV_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(contents.encode("utf-8")) > MAX_ENV_BYTES:
        raise ValueError("legacy environment is too large")
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        try:
            parsed = shlex.split(raw_value, comments=False, posix=True)
        except ValueError:
            continue
        if len(parsed) == 1:
            values[key] = parsed[0]
    return values


def first(values: dict[str, str], *keys: str) -> str:
    return next((values[key] for key in keys if values.get(key)), "")


def request_json(request: Request) -> tuple[int, dict]:
    try:
        with DIRECT_OPENER.open(request, timeout=15) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("eBay response is too large")
            decoded = json.loads(body)
            return response.status, decoded if isinstance(decoded, dict) else {}
    except HTTPError as exc:
        return exc.code, {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path("/opt/pokehunter/.env"))
    args = parser.parse_args()
    try:
        values = read_env(args.env)
    except (OSError, UnicodeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "stage": "configuration",
                    "error_type": type(exc).__name__,
                },
                separators=(",", ":"),
            )
        )
        return 1
    client_id = first(values, "EBAY_CLIENT_ID", "EBAY_APP_ID", "EBAY_CLIENTID")
    client_secret = first(values, "EBAY_CLIENT_SECRET", "EBAY_CERT_ID", "EBAY_CLIENTSECRET")
    campaign_id = first(values, "EBAY_AFFILIATE_CAMPAIGN_ID")
    postcode = first(values, "EBAY_DELIVERY_POSTCODE")
    if not client_id or not client_secret:
        print('{"status":"error","stage":"configuration","missing_credentials":true}')
        return 1
    if not re.fullmatch(r"\d{10}", campaign_id) or not re.fullmatch(
        r"[A-Za-z0-9 ]{2,10}", postcode
    ):
        print('{"status":"error","stage":"configuration","affiliate_or_postcode_invalid":true}')
        return 1

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    token_request = Request(
        "https://api.ebay.com/identity/v1/oauth2/token",
        data=urlencode(
            {
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            }
        ).encode(),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        oauth_status, token_body = request_json(token_request)
        token = token_body.get("access_token")
        if oauth_status != 200 or not isinstance(token, str):
            print(json.dumps({"status": "error", "stage": "oauth", "http": oauth_status}))
            return 1

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        }
        contexts = []
        if campaign_id:
            contexts.append(f"affiliateCampaignId={campaign_id}")
        if postcode:
            location = quote(f"country=GB,zip={postcode}", safe="")
            contexts.append(f"contextualLocation={location}")
        if contexts:
            headers["X-EBAY-C-ENDUSERCTX"] = ",".join(contexts)
        query = urlencode(
            {
                "q": "Charizard pokemon card",
                "category_ids": "183454",
                "filter": (
                    "deliveryCountry:GB,buyingOptions:{FIXED_PRICE},"
                    f"deliveryPostalCode:{postcode}"
                ),
                "limit": "3",
            }
        )
        browse_status, browse_body = request_json(
            Request(
                "https://api.ebay.com/buy/browse/v1/item_summary/search?" + query,
                headers=headers,
            )
        )
        items = browse_body.get("itemSummaries")
        if browse_status != 200 or not isinstance(items, list):
            print(json.dumps({"status": "error", "stage": "browse", "http": browse_status}))
            return 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "oauth_http": oauth_status,
                    "browse_http": browse_status,
                    "items_returned": len(items),
                    "affiliate_configured": bool(campaign_id),
                    "delivery_postcode_configured": bool(postcode),
                },
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"status": "error", "stage": "network", "error_type": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
