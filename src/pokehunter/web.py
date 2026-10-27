from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .alerts import AlertError, Telegram
from .catalog import refresh_catalog, reprice
from .config import Settings
from .db import Database, now_iso
from .ebay import EbayClient
from .matching import CONDITION_LABEL, VARIANT_LABEL
from .scanner import Scanner

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
OPEN_PATHS = ("/healthz",)


class Engine:
    """Owns the shared clients and runs the 15-minute background loop."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.db = Database(settings.database_path)
        self.http = http
        self.telegram = Telegram(settings, http)
        self.scanner = Scanner(
            self.db, settings, EbayClient(settings, http, self.db), self.telegram
        )
        self.wake = asyncio.Event()
        self.busy: str | None = None
        self.next_scan_at: str | None = None
        self.last_error: str | None = None

    def catalog_stale(self) -> bool:
        updated = self.db.get_kv("catalog_updated_at")
        if not updated:
            return True
        age = datetime.now(UTC) - datetime.fromisoformat(updated)
        return age > timedelta(hours=self.settings.catalog_refresh_hours)

    async def refresh_catalog(self) -> None:
        self.busy = "Updating card list and prices"
        try:
            await refresh_catalog(self.db, self.settings, self.http)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            log.exception("catalogue refresh failed")
            self.last_error = f"Card list update failed: {exc}"
        finally:
            self.busy = None

    async def scan(self) -> None:
        self.busy = "Scanning eBay"
        try:
            result = await self.scanner.scan()
            self.last_error = " | ".join(dict.fromkeys(result["errors"])) or None
        except Exception as exc:  # noqa: BLE001
            log.exception("scan failed")
            self.last_error = f"Scan failed: {exc}"
        finally:
            self.busy = None

    async def loop(self) -> None:
        # Apply any pricing-rule changes from an update straight away.
        with contextlib.suppress(Exception):
            reprice(self.db)
        while True:
            if self.catalog_stale():
                await self.refresh_catalog()
            if self.settings.ebay_ready and self.db.query("SELECT 1 FROM items LIMIT 1"):
                await self.scan()
            interval = self.settings.scan_interval_minutes * 60
            self.next_scan_at = (datetime.now(UTC) + timedelta(seconds=interval)).isoformat(
                timespec="seconds"
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=interval)
            self.wake.clear()


def _authorised(request: Request, password: str) -> bool:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        _, _, supplied = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(supplied.encode(), password.encode())


def create_app(settings: Settings | None = None, *, run_loop: bool = True) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        http = httpx.AsyncClient(headers={"User-Agent": "PokeHunter/1.0"})
        engine = Engine(settings, http)
        app.state.engine = engine
        task = asyncio.create_task(engine.loop()) if run_loop else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await http.aclose()
            engine.db.close()

    app = FastAPI(title="PokéHunter", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def password_gate(request: Request, call_next):
        password = settings.site_password.get_secret_value() if settings.site_password else ""
        if password and request.url.path not in OPEN_PATHS and not _authorised(request, password):
            return Response(
                "Password required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="PokeHunter"'},
            )
        return await call_next(request)

    def engine(request: Request) -> Engine:
        return request.app.state.engine

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        e = engine(request)
        last = e.db.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        counts = e.db.query(
            """
            SELECT c.pokemon, COUNT(*) AS total, SUM(i.owned) AS owned
            FROM items i JOIN cards c ON c.id = i.card_id
            WHERE i.hidden = 0 GROUP BY c.pokemon
            """
        )
        return {
            "busy": e.busy,
            "last_run": last,
            "next_scan_at": e.next_scan_at,
            "calls_today": e.db.calls_today(),
            "call_budget": settings.ebay_daily_call_budget,
            "catalog_updated_at": e.db.get_kv("catalog_updated_at"),
            "fx": e.db.get_kv("fx"),
            "ebay_ready": settings.ebay_ready,
            "telegram_ready": settings.telegram_ready,
            "error": e.last_error,
            "progress": counts,
            "interval_minutes": settings.scan_interval_minutes,
        }

    @app.get("/api/deals")
    async def deals(request: Request) -> dict[str, Any]:
        e = engine(request)
        return {
            "deals": e.scanner.deals(),
            "watch": e.scanner.priority_watch(),
            "conditions": CONDITION_LABEL,
            "variants": VARIANT_LABEL,
        }

    @app.get("/api/cards")
    async def cards(request: Request) -> dict[str, Any]:
        rows = engine(request).db.query(
            """
            SELECT i.id, i.card_id, i.variant, i.owned, i.hidden, i.priority, i.target_price,
                   i.market_gbp, i.price_source, c.pokemon, c.name, c.local_id, c.set_id,
                   c.set_name, c.set_official, c.release_date, c.rarity, c.image
            FROM items i JOIN cards c ON c.id = i.card_id
            ORDER BY c.release_date, c.set_id, CAST(c.local_id AS INTEGER), c.local_id,
                     CASE i.variant WHEN 'firstEdition' THEN 0 WHEN 'holo' THEN 1
                                    WHEN 'normal' THEN 2 ELSE 3 END
            """
        )
        return {"items": rows, "variants": VARIANT_LABEL}

    @app.post("/api/items/{item_id:path}")
    async def update_item(
        request: Request, item_id: str, changes: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        e = engine(request)
        if not e.db.one("SELECT id FROM items WHERE id=?", (item_id,)):
            raise HTTPException(404, "Unknown card")
        sets, values = [], []
        for key in ("owned", "hidden", "priority"):
            if key in changes:
                sets.append(f"{key}=?")
                values.append(1 if changes[key] else 0)
        if "target_price" in changes:
            raw = changes["target_price"]
            price = float(raw) if raw not in (None, "") else None
            if price is not None and price <= 0:
                price = None
            sets.append("target_price=?")
            values.append(price)
        if sets:
            with e.db.tx() as c:
                c.execute(f"UPDATE items SET {', '.join(sets)} WHERE id=?", (*values, item_id))
        if changes.get("priority"):
            e.wake.set()  # search a newly starred card straight away
        return e.db.one("SELECT * FROM items WHERE id=?", (item_id,)) or {}

    @app.post("/api/listings/{listing_id}/dismiss")
    async def dismiss(request: Request, listing_id: str) -> dict[str, bool]:
        with engine(request).db.tx() as c:
            c.execute("UPDATE listings SET dismissed=1 WHERE item_id=?", (listing_id,))
        return {"ok": True}

    @app.get("/api/prefs")
    async def get_prefs(request: Request) -> dict[str, Any]:
        return engine(request).db.prefs()

    @app.post("/api/prefs")
    async def set_prefs(request: Request, changes: dict[str, Any] = Body(...)) -> dict[str, Any]:
        e = engine(request)
        before = e.db.prefs()["price_source"]
        try:
            prefs = e.db.update_prefs(changes)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Invalid setting value") from exc
        if prefs["price_source"] != before:
            reprice(e.db)
        return prefs

    @app.post("/api/scan")
    async def scan_now(request: Request) -> dict[str, bool]:
        engine(request).wake.set()
        return {"ok": True}

    @app.post("/api/catalog/refresh")
    async def catalog_refresh(request: Request) -> dict[str, bool]:
        e = engine(request)
        e.db.set_kv("catalog_updated_at", None)
        e.wake.set()
        return {"ok": True}

    @app.post("/api/test-alert")
    async def test_alert(request: Request) -> dict[str, Any]:
        try:
            await engine(request).telegram.send(
                f"✅ PokéHunter is connected. Test sent {now_iso()[:16].replace('T', ' ')} UTC."
            )
        except AlertError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
