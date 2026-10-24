from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .ebay import EbayClient, decode_signature_header

logger = logging.getLogger(__name__)


def challenge_response(challenge_code: str, verification_token: str, endpoint: str) -> str:
    digest = hashlib.sha256()
    digest.update(challenge_code.encode("utf-8"))
    digest.update(verification_token.encode("utf-8"))
    digest.update(endpoint.encode("utf-8"))
    return digest.hexdigest()


class SignatureVerifier(Protocol):
    async def verify(self, body: bytes, signature_header: str) -> bool: ...


class EbaySignatureVerifier:
    def __init__(self, ebay: EbayClient) -> None:
        self.ebay = ebay

    async def verify(self, body: bytes, signature_header: str) -> bool:
        header = decode_signature_header(signature_header)
        public_key_pem = await self.ebay.get_public_key(header["kid"])
        try:
            signature = base64.b64decode(header["signature"], validate=True)
            key = serialization.load_pem_public_key(public_key_pem.replace("\\n", "\n").encode())
            if not isinstance(key, ec.EllipticCurvePublicKey):
                return False
            key.verify(signature, body, ec.ECDSA(hashes.SHA1()))
            return True
        except (ValueError, TypeError, InvalidSignature):
            return False


def validate_deletion_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object")
    metadata = payload.get("metadata")
    notification = payload.get("notification")
    if not isinstance(metadata, dict) or not isinstance(notification, dict):
        raise ValueError("Missing notification metadata")
    if metadata.get("topic") != "MARKETPLACE_ACCOUNT_DELETION":
        raise ValueError("Unexpected notification topic")
    notification_id = notification.get("notificationId")
    if not isinstance(notification_id, str) or not notification_id or len(notification_id) > 200:
        raise ValueError("Missing notification identifier")
    if not isinstance(notification.get("data"), dict):
        raise ValueError("Missing deletion data")
    return notification_id


def parse_json_body(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON payload") from exc
