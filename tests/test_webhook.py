from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from pokehunter.app import create_app
from pokehunter.webhook import (
    EbaySignatureVerifier,
    challenge_response,
    load_ebay_public_key,
)

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


def ebay_test_payload() -> dict:
    return {
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
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"challengeResponse": expected}

    legacy_path_response = asyncio.run(
        asgi_request(
            app,
            "GET",
            "/ebay/deletion",
            params={"challenge_code": "challenge"},
        )
    )
    assert legacy_path_response.status_code == 200
    assert legacy_path_response.json() == {"challengeResponse": expected}


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

    legacy_path_response = asyncio.run(
        asgi_request(
            app,
            "POST",
            "/ebay/deletion",
            json=payload(),
            headers={"X-EBAY-SIGNATURE": "valid"},
        )
    )
    assert legacy_path_response.status_code == 204


def test_eias_only_notification_is_valid_but_missing_identity_is_rejected(settings):
    eias_only = payload()
    eias_only["notification"]["data"] = {"eiasToken": "test-token"}
    accepted = asyncio.run(
        asgi_request(
            create_app(settings, verifier=AcceptingVerifier()),
            "POST",
            "/ebay/deletion",
            json=eias_only,
            headers={"X-EBAY-SIGNATURE": "valid"},
        )
    )
    assert accepted.status_code == 204

    missing_identity = payload()
    missing_identity["notification"]["data"] = {}
    rejected = asyncio.run(
        asgi_request(
            create_app(settings, verifier=AcceptingVerifier()),
            "POST",
            "/ebay/deletion",
            json=missing_identity,
            headers={"X-EBAY-SIGNATURE": "valid"},
        )
    )
    assert rejected.status_code == 412


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


def test_oversized_signature_header_is_rejected_before_verification(settings):
    response = asyncio.run(
        asgi_request(
            create_app(settings, verifier=AcceptingVerifier()),
            "POST",
            "/webhooks/ebay/account-deletion",
            json=payload(),
            headers={"X-EBAY-SIGNATURE": "x" * 4097},
        )
    )
    assert response.status_code == 412


def test_oversized_notification_body_is_rejected(settings):
    response = asyncio.run(
        asgi_request(
            create_app(settings, verifier=AcceptingVerifier()),
            "POST",
            "/ebay/deletion",
            content=b"x" * (64 * 1024 + 1),
            headers={"Content-Type": "application/json", "X-EBAY-SIGNATURE": "valid"},
        )
    )
    assert response.status_code == 413


def test_signature_verifier_validates_raw_and_canonical_body():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    body = json.dumps(payload(), separators=(",", ":")).encode()
    signature = private_key.sign(body, ec.ECDSA(hashes.SHA1()))
    header = base64.b64encode(
        json.dumps(
            {
                "alg": "ecdsa",
                "kid": "key-1",
                "signature": base64.b64encode(signature).decode(),
                "digest": "SHA1",
            }
        ).encode()
    ).decode()

    class FakeEbay:
        async def get_public_key(self, key_id: str) -> str:
            assert key_id == "key-1"
            return public_pem

    verifier = EbaySignatureVerifier(FakeEbay())  # type: ignore[arg-type]
    assert asyncio.run(verifier.verify(body, header)) is True
    assert asyncio.run(verifier.verify(body + b" \n", header)) is True
    assert (
        asyncio.run(verifier.verify(body.replace(b"notification-1", b"notification-2"), header))
        is False
    )


def test_official_ebay_notification_fixture_and_compact_public_key_are_valid():
    body = json.dumps(ebay_test_payload(), separators=(",", ":")).encode()

    class FakeEbay:
        async def get_public_key(self, key_id: str) -> str:
            assert key_id == "9936261a-7d7b-4621-a0f1-96ccb428af49"
            return EBAY_TEST_PUBLIC_KEY

    verifier = EbaySignatureVerifier(FakeEbay())  # type: ignore[arg-type]
    assert asyncio.run(verifier.verify(body, EBAY_TEST_SIGNATURE)) is True
    tampered = body.replace(b"test_user", b"other_user")
    assert asyncio.run(verifier.verify(tampered, EBAY_TEST_SIGNATURE)) is False


def test_public_key_loader_rejects_non_ec_and_trailing_data():
    private_key = ec.generate_private_key(ec.SECP256R1())
    compact = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
        .replace("\n", "")
    )
    assert isinstance(load_ebay_public_key(compact), ec.EllipticCurvePublicKey)
    try:
        load_ebay_public_key(compact + "trailing")
    except ValueError:
        pass
    else:
        raise AssertionError("public key with trailing data was accepted")


def test_invalid_inner_signature_is_rejected_without_key_lookup():
    header = base64.b64encode(
        json.dumps({"alg": "ecdsa", "kid": "key-1", "signature": "***", "digest": "SHA1"}).encode()
    ).decode()

    class FakeEbay:
        async def get_public_key(self, key_id: str) -> str:
            raise AssertionError("invalid signature triggered a public-key request")

    verifier = EbaySignatureVerifier(FakeEbay())  # type: ignore[arg-type]
    body = json.dumps(payload(), separators=(",", ":")).encode()
    assert asyncio.run(verifier.verify(body, header)) is False


def test_challenge_response_is_deterministic():
    assert challenge_response("a", "b", "c") == hashlib.sha256(b"abc").hexdigest()
