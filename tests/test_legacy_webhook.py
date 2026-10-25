from __future__ import annotations

import base64
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec

# Published by eBay in the official Event Notification Node.js SDK test fixture:
# https://github.com/eBay/event-notification-nodejs-sdk/blob/main/test/test.json
EBAY_TEST_SIGNATURE = (
    "eyJhbGciOiJlY2RzYSIsImtpZCI6Ijk5MzYyNjFhLTdkN2ItNDYyMS1hMGYxLTk2Y2NiNDI4YWY0"
    "OSIsInNpZ25hdHVyZSI6Ik1FWUNJUUNmeGZJV3V4bVdjSUJRSjljNS9YN2lHREpxczJSQ0dzQkVhQWpp"
    "bnlycmZBSWhBSVY2d0djVGlCdVY1S0pVaWYyaG9reXJMK1E5c3NIa2FkK214Mm5FRTI1dyIsImRpZ2Vz"
    "dCI6IlNIQTEifQ=="
)
EBAY_TEST_PUBLIC_KEY = (
    "-----BEGIN PUBLIC KEY-----"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEZhhxXKtR+TOvtDbgTPCkSof02qgBB7IsYOyf76ilExJ/"
    "upAa/vKIKheOoCyOpcLmi4t0b4uepb7LLjmMr90FUg=="
    "-----END PUBLIC KEY-----"
)
EBAY_TEST_PAYLOAD = {
    "metadata": {
        "topic": "MARKETPLACE_ACCOUNT_DELETION",
        "schemaVersion": "1.0",
        "deprecated": False,
    },
    "notification": {
        "notificationId": (
            "49feeaeb-4982-42d9-a377-9645b8479411_33f7e043-fed8-442b-9d44-791923bd9a6d"
        ),
        "eventDate": "2021-03-19T20:43:59.462Z",
        "publishDate": "2021-03-19T20:43:59.679Z",
        "publishAttemptCount": 1,
        "data": {
            "username": "test_user",
            "userId": "ma8vp1jySJC",
            "eiasToken": "nY+sHZ2PrBmdj6wVnY+sEZ2PrA2dj6wJnY+gAZGEpwmdj6x9nY+seQ==",
        },
    },
}


def load_legacy_module() -> ModuleType:
    path = Path(__file__).parents[1] / "app" / "ebay_notifications.py"
    spec = importlib.util.spec_from_file_location("legacy_ebay_notifications", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_handler_verifies_official_test_notification(monkeypatch, tmp_path):
    legacy = load_legacy_module()
    key = legacy.load_ebay_public_key(EBAY_TEST_PUBLIC_KEY)
    assert isinstance(key, ec.EllipticCurvePublicKey)
    monkeypatch.setattr(legacy, "get_public_key", lambda kid: key)
    monkeypatch.setattr(legacy, "DB", tmp_path / "deletions.sqlite3")
    legacy.init_db()

    body = json.dumps(EBAY_TEST_PAYLOAD, separators=(",", ":")).encode()
    message = legacy.verify(body, EBAY_TEST_SIGNATURE)
    legacy.process_deletion(message)

    with sqlite3.connect(legacy.DB) as conn:
        row = conn.execute("SELECT notification_id, username, user_id FROM deletions").fetchone()
    assert row == (
        EBAY_TEST_PAYLOAD["notification"]["notificationId"],
        "test_user",
        "ma8vp1jySJC",
    )


def test_legacy_handler_rejects_tampering_and_unsupported_signature_parameters(monkeypatch):
    legacy = load_legacy_module()
    key = legacy.load_ebay_public_key(EBAY_TEST_PUBLIC_KEY)
    monkeypatch.setattr(legacy, "get_public_key", lambda kid: key)
    body = json.dumps(EBAY_TEST_PAYLOAD, separators=(",", ":")).encode()

    with pytest.raises(InvalidSignature):
        legacy.verify(body.replace(b"test_user", b"other_user"), EBAY_TEST_SIGNATURE)

    envelope = json.loads(base64.b64decode(EBAY_TEST_SIGNATURE))
    envelope["alg"] = "rsa"
    invalid_header = base64.b64encode(json.dumps(envelope).encode()).decode()
    with pytest.raises(ValueError, match="Unsupported signature"):
        legacy.verify(body, invalid_header)


def test_legacy_process_deletion_rejects_missing_identity():
    legacy = load_legacy_module()
    payload = json.loads(json.dumps(EBAY_TEST_PAYLOAD))
    payload["notification"]["data"] = {}
    with pytest.raises(ValueError, match="Missing user identifier"):
        legacy.process_deletion(payload)


def test_legacy_process_deletion_accepts_eias_only_without_storing_it(monkeypatch, tmp_path):
    legacy = load_legacy_module()
    monkeypatch.setattr(legacy, "DB", tmp_path / "deletions.sqlite3")
    legacy.init_db()
    payload = json.loads(json.dumps(EBAY_TEST_PAYLOAD))
    payload["notification"]["data"] = {"eiasToken": "test-token"}
    legacy.process_deletion(payload)
    with sqlite3.connect(legacy.DB) as conn:
        row = conn.execute("SELECT username, user_id FROM deletions").fetchone()
    assert row == (None, None)
