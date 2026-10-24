from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from pokehunter.app import create_app
from pokehunter.webhook import EbaySignatureVerifier, challenge_response


class AcceptingVerifier:
    async def verify(self, body: bytes, signature_header: str) -> bool:
        return signature_header == "valid"


async def asgi_request(app, method: str, path: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        return await client.request(method, path, **kwargs)


def payload() -> dict:
    return {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION", "schemaVersion": "1.0"},
        "notification": {
            "notificationId": "notification-1",
            "data": {"username": "must-not-be-logged", "userId": "private-id"},
        },
    }


def test_challenge_hash_order_and_json_content_type(settings):
    app = create_app(settings, verifier=AcceptingVerifier())
    response = asyncio.run(
        asgi_request(
            app,
            "GET",
            "/webhooks/ebay/account-deletion",
            params={"challenge_code": "challenge"},
        )
    )
    expected = hashlib.sha256(
        b"challenge"
        b"a_valid_verification_token_123456"
        b"https://cards.example/webhooks/ebay/account-deletion"
    ).hexdigest()
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"challengeResponse": expected}


def test_valid_notification_returns_204(settings):
    app = create_app(settings, verifier=AcceptingVerifier())
    response = asyncio.run(
        asgi_request(
            app,
            "POST",
            "/webhooks/ebay/account-deletion",
            json=payload(),
            headers={"X-EBAY-SIGNATURE": "valid"},
        )
    )
    assert response.status_code == 204
    assert response.content == b""


def test_missing_or_bad_signature_is_rejected(settings):
    missing = asyncio.run(
        asgi_request(
            create_app(settings, verifier=AcceptingVerifier()),
            "POST",
            "/webhooks/ebay/account-deletion",
            json=payload(),
        )
    )
    bad = asyncio.run(
        asgi_request(
            create_app(settings, verifier=AcceptingVerifier()),
            "POST",
            "/webhooks/ebay/account-deletion",
            json=payload(),
            headers={"X-EBAY-SIGNATURE": "bad"},
        )
    )
    assert missing.status_code == 412
    assert bad.status_code == 412


def test_signature_verifier_validates_raw_body():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    body = json.dumps(payload(), separators=(",", ":")).encode()
    signature = private_key.sign(body, ec.ECDSA(hashes.SHA1()))
    header = base64.b64encode(
        json.dumps({"kid": "key-1", "signature": base64.b64encode(signature).decode()}).encode()
    ).decode()

    class FakeEbay:
        async def get_public_key(self, key_id: str) -> str:
            assert key_id == "key-1"
            return public_pem

    verifier = EbaySignatureVerifier(FakeEbay())  # type: ignore[arg-type]
    assert asyncio.run(verifier.verify(body, header)) is True
    assert asyncio.run(verifier.verify(body + b" ", header)) is False


def test_challenge_response_is_deterministic():
    assert challenge_response("a", "b", "c") == hashlib.sha256(b"abc").hexdigest()
