"""Legacy production webhook retained solely for the pre-migration hotfix.

The systemd service currently deployed on the server executes this file directly.
The replacement ASGI webhook lives in ``pokehunter.app`` and will supersede this
module after the separately approved migration.
"""

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

BASE = Path(__file__).resolve().parent.parent
TOKEN_PATH = BASE / "data/ebay_verification_token"
ENDPOINT = "https://pokehunter-nq.duckdns.org/ebay/deletion"
DB = BASE / "data/ebay_deletions.sqlite3"
KEY_CACHE = {}
CACHE_LOCK = threading.Lock()
DB_LOCK = threading.Lock()
PUBLIC_KEY_BEGIN = "-----BEGIN PUBLIC KEY-----"
PUBLIC_KEY_END = "-----END PUBLIC KEY-----"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def credentials():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    client_id = (
        os.environ.get("EBAY_CLIENT_ID")
        or env.get("EBAY_CLIENT_ID")
        or env.get("EBAY_APP_ID")
        or env.get("EBAY_CLIENTID")
    )
    client_secret = (
        os.environ.get("EBAY_CLIENT_SECRET")
        or env.get("EBAY_CLIENT_SECRET")
        or env.get("EBAY_CERT_ID")
        or env.get("EBAY_CLIENTSECRET")
    )
    if not client_id or not client_secret:
        raise RuntimeError(
            "eBay credentials not found in .env; expected EBAY_CLIENT_ID and EBAY_CLIENT_SECRET"
        )
    return client_id, client_secret


def load_ebay_public_key(value):
    """Load the compact PEM representation returned by eBay's Notification API."""
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("Invalid eBay public key")
    normalized = value.strip().replace("\\n", "\n")
    if normalized.count(PUBLIC_KEY_BEGIN) != 1 or normalized.count(PUBLIC_KEY_END) != 1:
        raise ValueError("Invalid eBay public key")
    start = normalized.index(PUBLIC_KEY_BEGIN) + len(PUBLIC_KEY_BEGIN)
    end = normalized.index(PUBLIC_KEY_END, start)
    if normalized[end + len(PUBLIC_KEY_END) :].strip():
        raise ValueError("Invalid eBay public key")
    encoded_key = "".join(normalized[start:end].split())
    if not encoded_key:
        raise ValueError("Invalid eBay public key")
    pem = f"{PUBLIC_KEY_BEGIN}\n{encoded_key}\n{PUBLIC_KEY_END}\n"
    key = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("Unsupported eBay public key type")
    return key


def get_public_key(kid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", kid):
        raise ValueError("Invalid key ID")
    now = time.monotonic()
    with CACHE_LOCK:
        cached = KEY_CACHE.get(kid)
        if cached and cached[1] > now:
            return cached[0]
    client_id, client_secret = credentials()
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    ).encode()
    request = Request(
        "https://api.ebay.com/identity/v1/oauth2/token",
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urlopen(request, timeout=12) as response:
        access_token = json.load(response)["access_token"]
    request = Request(
        "https://api.ebay.com/commerce/notification/v1/public_key/" + quote(kid, safe=""),
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    with urlopen(request, timeout=12) as response:
        public_key = json.load(response)["key"]
    key = load_ebay_public_key(public_key)
    with CACHE_LOCK:
        KEY_CACHE[kid] = (key, now + 3600)
    return key


def verify(body, signature_header):
    if not signature_header or len(signature_header) > 4096:
        raise ValueError("Missing or oversized signature header")
    envelope = json.loads(base64.b64decode(signature_header, validate=True))
    if not isinstance(envelope, dict):
        raise ValueError("Invalid signature header")
    algorithm = envelope.get("alg")
    digest = envelope.get("digest")
    kid = envelope.get("kid")
    encoded_signature = envelope.get("signature")
    if not all(
        isinstance(item, str) and item for item in (algorithm, digest, kid, encoded_signature)
    ):
        raise ValueError("Invalid signature header")
    if algorithm.casefold() != "ecdsa" or digest.upper() != "SHA1":
        raise ValueError("Unsupported signature parameters")
    signature = base64.b64decode(encoded_signature, validate=True)
    if not signature or len(signature) > 512:
        raise ValueError("Invalid signature")
    key = get_public_key(kid)
    message = json.loads(body)
    canonical = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
    candidates = (body,) if canonical == body else (body, canonical)
    for candidate in candidates:
        try:
            key.verify(signature, candidate, ec.ECDSA(hashes.SHA1()))
            return message
        except InvalidSignature:
            continue
    raise InvalidSignature("Notification signature did not match")


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS deletions ("
            "notification_id TEXT PRIMARY KEY, username TEXT, user_id TEXT, "
            "received_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )


def process_deletion(message):
    if not isinstance(message, dict):
        raise ValueError("Invalid notification")
    metadata = message.get("metadata")
    notification = message.get("notification")
    if not isinstance(metadata, dict) or not isinstance(notification, dict):
        raise ValueError("Missing notification metadata")
    if metadata.get("topic") != "MARKETPLACE_ACCOUNT_DELETION":
        raise ValueError("Unexpected notification topic")
    payload = notification.get("data")
    notification_id = notification.get("notificationId")
    if not isinstance(payload, dict):
        raise ValueError("Missing deletion data")
    if not isinstance(notification_id, str) or not notification_id or len(notification_id) > 256:
        raise ValueError("Missing notification ID")
    username = payload.get("username")
    user_id = payload.get("userId")
    eias_token = payload.get("eiasToken")
    if not any(isinstance(item, str) and item for item in (username, user_id, eias_token)):
        raise ValueError("Missing user identifier")
    if username is not None and (not isinstance(username, str) or len(username) > 1024):
        raise ValueError("Invalid username")
    if user_id is not None and (not isinstance(user_id, str) or len(user_id) > 1024):
        raise ValueError("Invalid user ID")
    if eias_token is not None and (not isinstance(eias_token, str) or len(eias_token) > 2048):
        raise ValueError("Invalid EIAS token")
    with DB_LOCK, sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO deletions (notification_id, username, user_id) VALUES (?, ?, ?)",
            (notification_id, username, user_id),
        )


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data=b"", content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        if data:
            self.send_header("Content-Type", content_type)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != "/ebay/deletion":
            return self.respond(404)
        challenge = parse_qs(url.query).get("challenge_code", [None])[0]
        if not challenge:
            return self.respond(400)
        token = TOKEN_PATH.read_text().strip()
        digest = hashlib.sha256((challenge + token + ENDPOINT).encode()).hexdigest()
        self.respond(200, json.dumps({"challengeResponse": digest}).encode())

    def do_POST(self):
        if urlsplit(self.path).path != "/ebay/deletion":
            return self.respond(404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 65536:
                return self.respond(413)
            body = self.rfile.read(length)
            message = verify(body, self.headers.get("X-EBAY-SIGNATURE"))
            process_deletion(message)
        except (InvalidSignature, ValueError, KeyError, TypeError, json.JSONDecodeError):
            logging.warning("Rejected invalid eBay notification")
            return self.respond(412)
        except (HTTPError, URLError, TimeoutError, RuntimeError, OSError, sqlite3.Error):
            logging.exception("eBay notification could not be processed")
            return self.respond(503)
        return self.respond(204)


if __name__ == "__main__":
    init_db()
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
