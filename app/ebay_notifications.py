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
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

BASE = Path(__file__).resolve().parent.parent
TOKEN = (BASE / 'data/ebay_verification_token').read_text().strip()
ENDPOINT = 'https://pokehunter-nq.duckdns.org/ebay/deletion'
DB = BASE / 'data/ebay_deletions.sqlite3'
KEY_CACHE = {}
CACHE_LOCK = threading.Lock()
DB_LOCK = threading.Lock()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def credentials():
    env = {}
    for line in (BASE / '.env').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    client_id = (os.environ.get('EBAY_CLIENT_ID') or env.get('EBAY_CLIENT_ID') or env.get('EBAY_APP_ID') or env.get('EBAY_CLIENTID'))
    client_secret = (os.environ.get('EBAY_CLIENT_SECRET') or env.get('EBAY_CLIENT_SECRET') or env.get('EBAY_CERT_ID') or env.get('EBAY_CLIENTSECRET'))
    if not client_id or not client_secret:
        raise RuntimeError('eBay credentials not found in .env; expected EBAY_CLIENT_ID and EBAY_CLIENT_SECRET')
    return client_id, client_secret


def get_public_key(kid):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', kid):
        raise ValueError('Invalid key ID')
    now = time.monotonic()
    with CACHE_LOCK:
        cached = KEY_CACHE.get(kid)
        if cached and cached[1] > now:
            return cached[0]
    client_id, client_secret = credentials()
    auth = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    body = urlencode({'grant_type': 'client_credentials', 'scope': 'https://api.ebay.com/oauth/api_scope'}).encode()
    request = Request('https://api.ebay.com/identity/v1/oauth2/token', data=body, headers={'Authorization': f'Basic {auth}', 'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    with urlopen(request, timeout=12) as response:
        access_token = json.load(response)['access_token']
    request = Request('https://api.ebay.com/commerce/notification/v1/public_key/' + quote(kid, safe=''), headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'})
    with urlopen(request, timeout=12) as response:
        public_key = json.load(response)['key']
    key = serialization.load_pem_public_key(public_key.encode('ascii'))
    with CACHE_LOCK:
        KEY_CACHE[kid] = (key, now + 3600)
    return key


def verify(body, signature_header):
    if not signature_header or len(signature_header) > 4096:
        raise ValueError('Missing or oversized signature header')
    envelope = json.loads(base64.b64decode(signature_header, validate=True))
    kid = envelope['kid']
    signature = base64.b64decode(envelope['signature'], validate=True)
    key = get_public_key(kid)
    # eBay Node SDK uses crypto.createVerify('ssl3-sha1') on JSON.stringify(message).
    # Python preserves incoming JSON body bytes. eBay sends compact JSON; verify
    # the raw bytes first, then the Node JSON.stringify equivalent for whitespace.
    message = json.loads(body)
    candidates = [body, json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')]
    for candidate in candidates:
        try:
            if isinstance(key, ec.EllipticCurvePublicKey):
                key.verify(signature, candidate, ec.ECDSA(hashes.SHA1()))
            elif isinstance(key, rsa.RSAPublicKey):
                key.verify(signature, candidate, padding.PKCS1v15(), hashes.SHA1())
            else:
                raise ValueError('Unsupported public key type')
            return message
        except InvalidSignature:
            continue
    raise InvalidSignature('Notification signature did not match')


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS deletions (notification_id TEXT PRIMARY KEY, username TEXT, user_id TEXT, received_at TEXT DEFAULT CURRENT_TIMESTAMP)')


def process_deletion(message):
    if message.get('metadata', {}).get('topic') != 'MARKETPLACE_ACCOUNT_DELETION':
        raise ValueError('Unexpected notification topic')
    payload = message.get('notification', {}).get('data', {})
    notification_id = message.get('notification', {}).get('notificationId') or message.get('metadata', {}).get('notificationId')
    if not isinstance(notification_id, str) or not notification_id or len(notification_id) > 256:
        raise ValueError('Missing notification ID')
    username = payload.get('username')
    user_id = payload.get('userId')
    if not username and not user_id:
        raise ValueError('Missing user identifier')
    # This service currently holds no user account records. Record minimal
    # deletion requests for future scanner integrations, without storing payloads.
    with DB_LOCK, sqlite3.connect(DB) as conn:
        conn.execute('INSERT OR IGNORE INTO deletions (notification_id, username, user_id) VALUES (?, ?, ?)', (notification_id, username, user_id))


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data=b'', content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Length', str(len(data)))
        if data:
            self.send_header('Content-Type', content_type)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != '/ebay/deletion':
            return self.respond(404)
        challenge = parse_qs(url.query).get('challenge_code', [None])[0]
        if not challenge:
            return self.respond(400)
        digest = hashlib.sha256((challenge + TOKEN + ENDPOINT).encode('utf-8')).hexdigest()
        self.respond(200, json.dumps({'challengeResponse': digest}).encode())

    def do_POST(self):
        if urlsplit(self.path).path != '/ebay/deletion':
            return self.respond(404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 2 or length > 65536:
                return self.respond(413)
            body = self.rfile.read(length)
            message = verify(body, self.headers.get('X-EBAY-SIGNATURE'))
            process_deletion(message)
        except (InvalidSignature, ValueError, KeyError, TypeError, json.JSONDecodeError):
            logging.warning('Rejected invalid eBay notification')
            return self.respond(400)
        except (HTTPError, URLError, TimeoutError, RuntimeError, OSError, sqlite3.Error):
            logging.exception('eBay notification could not be processed')
            return self.respond(503)
        return self.respond(204)


if __name__ == '__main__':
    init_db()
    ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
