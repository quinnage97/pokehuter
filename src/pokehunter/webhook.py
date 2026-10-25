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

_PUBLIC_KEY_BEGIN = "-----BEGIN PUBLIC KEY-----"
_PUBLIC_KEY_END = "-----END PUBLIC KEY-----"


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
        try:
            signature = base64.b64decode(header["signature"], validate=True)
            if not signature or len(signature) > 512:
                return False
            public_key_pem = await self.ebay.get_public_key(header["kid"])
            key = load_ebay_public_key(public_key_pem)
            if not isinstance(key, ec.EllipticCurvePublicKey):
                return False
            # eBay's reference SDK verifies JSON.stringify(message), so try the exact body and
            # its compact, insertion-order-preserving JSON representation. This tolerates
            # semantically irrelevant transport whitespace without weakening payload integrity.
            payload = parse_json_body(body)
            canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            candidates = (body,) if canonical == body else (body, canonical)
            for candidate in candidates:
                try:
                    key.verify(signature, candidate, ec.ECDSA(hashes.SHA1()))
                    return True
                except InvalidSignature:
                    continue
            return False
        except (ValueError, TypeError, InvalidSignature):
            return False


def load_ebay_public_key(value: str) -> ec.EllipticCurvePublicKey:
    """Load the compact PEM format returned by eBay's getPublicKey endpoint."""
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("Invalid eBay public key")
    normalized = value.strip().replace("\\n", "\n")
    if normalized.count(_PUBLIC_KEY_BEGIN) != 1 or normalized.count(_PUBLIC_KEY_END) != 1:
        raise ValueError("Invalid eBay public key")
    start = normalized.index(_PUBLIC_KEY_BEGIN) + len(_PUBLIC_KEY_BEGIN)
    end = normalized.index(_PUBLIC_KEY_END, start)
    if normalized[end + len(_PUBLIC_KEY_END) :].strip():
        raise ValueError("Invalid eBay public key")
    encoded_key = "".join(normalized[start:end].split())
    if not encoded_key:
        raise ValueError("Invalid eBay public key")
    # eBay's documented test response has no line breaks around the Base64 key body.
    pem = f"{_PUBLIC_KEY_BEGIN}\n{encoded_key}\n{_PUBLIC_KEY_END}\n"
    key = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("Unsupported eBay public key type")
    return key


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
    data = notification.get("data")
    if not isinstance(data, dict):
        raise ValueError("Missing deletion data")
    identifiers = (data.get("username"), data.get("userId"), data.get("eiasToken"))
    if not any(isinstance(item, str) and item for item in identifiers):
        raise ValueError("Missing user identifier")
    if any(
        item is not None and (not isinstance(item, str) or len(item) > 2048) for item in identifiers
    ):
        raise ValueError("Invalid user identifier")
    return notification_id


def parse_json_body(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON payload") from exc
