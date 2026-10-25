from __future__ import annotations

import base64
import importlib.util
import json
import os
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec


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


def load_module():
    default = Path(__file__).parents[1] / "app" / "ebay_notifications.py"
    path = Path(os.environ.get("HOTFIX_MODULE", default))
    spec = importlib.util.spec_from_file_location("legacy_ebay_notifications", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EbayNotificationVerificationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.key = self.module.load_ebay_public_key(EBAY_TEST_PUBLIC_KEY)
        self.assertIsInstance(self.key, ec.EllipticCurvePublicKey)
        self.module.get_public_key = lambda kid: self.key
        self.body = json.dumps(EBAY_TEST_PAYLOAD, separators=(",", ":")).encode()

    def test_official_compact_pem_fixture_verifies(self):
        message = self.module.verify(self.body, EBAY_TEST_SIGNATURE)
        self.assertEqual(message, EBAY_TEST_PAYLOAD)

    def test_tampered_notification_is_rejected(self):
        with self.assertRaises(InvalidSignature):
            self.module.verify(
                self.body.replace(b"test_user", b"other_user"),
                EBAY_TEST_SIGNATURE,
            )

    def test_unsupported_signature_parameters_are_rejected(self):
        envelope = json.loads(base64.b64decode(EBAY_TEST_SIGNATURE))
        envelope["alg"] = "rsa"
        invalid_header = base64.b64encode(json.dumps(envelope).encode()).decode()
        with self.assertRaisesRegex(ValueError, "Unsupported signature"):
            self.module.verify(self.body, invalid_header)


if __name__ == "__main__":
    unittest.main()
