from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from .config import Settings
from .ebay import EbayClient
from .webhook import (
    EbaySignatureVerifier,
    SignatureVerifier,
    challenge_response,
    parse_json_body,
    validate_deletion_payload,
)

logger = logging.getLogger(__name__)
MAX_NOTIFICATION_BYTES = 64 * 1024


def create_app(
    settings: Settings | None = None,
    *,
    verifier: SignatureVerifier | None = None,
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await app.state.http.aclose()

    app = FastAPI(
        title="PokéHunter",
        docs_url=None,
        redoc_url=None,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    app.state.verifier = verifier or EbaySignatureVerifier(EbayClient(config, app.state.http))

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/webhooks/ebay/account-deletion")
    async def verify_endpoint(
        challenge_code: str = Query(..., min_length=1, max_length=512),
    ) -> JSONResponse:
        try:
            endpoint, token = config.require_webhook_config()
        except ValueError as exc:
            logger.error("eBay webhook is not configured", extra={"event": "webhook_config_error"})
            raise HTTPException(status_code=503, detail="Webhook is not configured") from exc
        return JSONResponse(
            {"challengeResponse": challenge_response(challenge_code, token, endpoint)}
        )

    @app.post("/webhooks/ebay/account-deletion")
    async def receive_deletion(
        request: Request,
        x_ebay_signature: str | None = Header(default=None),
    ) -> Response:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
            "application/json"
        ):
            raise HTTPException(status_code=415, detail="Content-Type must be application/json")
        if not x_ebay_signature:
            raise HTTPException(status_code=412, detail="Missing notification signature")
        body = await request.body()
        if not body or len(body) > MAX_NOTIFICATION_BYTES:
            raise HTTPException(status_code=413, detail="Invalid notification size")
        try:
            payload = parse_json_body(body)
            notification_id = validate_deletion_payload(payload)
            valid = await app.state.verifier.verify(body, x_ebay_signature)
        except Exception as exc:
            logger.warning(
                "eBay notification validation failed",
                extra={"event": "notification_rejected"},
            )
            raise HTTPException(status_code=412, detail="Invalid notification") from exc
        if not valid:
            raise HTTPException(status_code=412, detail="Invalid notification signature")

        # PokéHunter never stores eBay usernames, user IDs, EIAS tokens, or seller details.
        # The deletion data is deliberately discarded after signature/topic validation.
        logger.info(
            "eBay account deletion notification acknowledged",
            extra={
                "event": "account_deletion_acknowledged",
                "notification_id": hashlib.sha256(notification_id.encode()).hexdigest()[:16],
            },
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()
