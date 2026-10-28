from __future__ import annotations

import asyncio
import base64
import contextlib
import json
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
from .filters import clean
from .learning import learn, record_fake, summary, undo_fake
from .matching import CONDITION_LABEL, VARIANT_LABEL, variant_label
from .photocheck import PhotoChecker
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
        self.photos = PhotoChecker(settings, http, self.db)
        self.scanner = Scanner(
            self.db, settings, EbayClient(settings, http, self.db), self.telegram, self.photos
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
        # Apply pricing and filter changes from an update straight away, rather than at the
        # end of the first scan (junk caught by new filters disappears immediately).
        with contextlib.suppress(Exception):
            reprice(self.db)
        try:
            await asyncio.to_thread(self.scanner.refresh_flags)
        except Exception:  # noqa: BLE001
            log.exception("start-up recheck failed")
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
            "photo_check_ready": e.photos.ready,
            "photo_checks_today": int(e.db.get_kv(f"photo_checks:{now_iso()[:10]}", 0) or 0),
            "photo_checks_per_day": e.db.prefs()["photo_checks_per_day"],
            "error": e.last_error,
            "progress": counts,
            "interval_minutes": settings.scan_interval_minutes,
        }

    def labelled(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            if row.get("variant"):
                row["variant_label"] = variant_label(row["variant"])
        return rows

    @app.get("/api/deals")
    async def deals(request: Request, everything: bool = False) -> dict[str, Any]:
        e = engine(request)
        return {
            "deals": labelled(e.scanner.listings(deals_only=not everything)),
            "conditions": CONDITION_LABEL,
            "variants": VARIANT_LABEL,
        }

    @app.get("/api/cards")
    async def cards(request: Request) -> dict[str, Any]:
        rows = engine(request).db.query(
            """
            SELECT i.id, i.card_id, i.variant, i.owned, i.owned_at, i.hidden, i.priority,
                   i.target_price,
                   i.market_gbp, i.price_source, i.ebay_median_gbp, i.ebay_comps,
                   c.pokemon, c.name, c.local_id, c.set_id, c.set_name, c.set_official,
                   c.release_date, c.serie_name, c.rarity, c.image, c.image_alt, c.custom,
                   c.image_override, cheap.listing_id AS cheapest_listing,
                   cheap.total AS cheapest_total, cheap.condition AS cheapest_condition
            FROM items i JOIN cards c ON c.id = i.card_id
            LEFT JOIN (
                SELECT match_item_id, item_id AS listing_id, MIN(total) AS total, condition
                FROM listings
                WHERE dismissed = 0 AND sold = 0 AND last_seen >= :cutoff
                  AND user_verdict IS NULL
                  AND COALESCE(photo_verdict, '') NOT IN ('fake', 'not_a_card', 'wrong_card')
                  AND COALESCE(warning, '') NOT LIKE '%Likely fake%'
                GROUP BY match_item_id
            ) cheap ON cheap.match_item_id = i.id
            ORDER BY c.release_date, c.set_id, CAST(c.local_id AS INTEGER), c.local_id,
                     CASE i.variant WHEN 'firstEdition' THEN 0 WHEN 'holo' THEN 1
                                    WHEN 'normal' THEN 2 ELSE 3 END
            """,
            {"cutoff": (datetime.now(UTC) - timedelta(hours=36)).isoformat()},
        )
        return {"items": labelled(rows), "variants": VARIANT_LABEL}

    @app.post("/api/cards/custom")
    async def add_card(request: Request, card: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Add a card TCGdex doesn't know about, so it can be ticked off and searched for."""
        e = engine(request)
        name = str(card.get("name") or "").strip()
        local_id = str(card.get("local_id") or "").strip()
        pokemon = str(card.get("pokemon") or "").strip()
        if not name or not local_id or pokemon not in settings.pokemon_names:
            raise HTTPException(400, "Name, number and Pokémon are required")
        try:
            official = int(card["set_official"]) if card.get("set_official") else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Set size must be a number") from exc
        variants = [v for v in card.get("variants") or ["holo"] if v in VARIANT_LABEL] or ["holo"]
        card_id = f"custom-{secrets.token_hex(4)}"
        with e.db.tx() as c:
            c.execute(
                """
                INSERT INTO cards (id, pokemon, name, local_id, set_id, set_name, set_official,
                    release_date, image, custom, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    card_id,
                    pokemon,
                    name,
                    local_id,
                    "custom",
                    str(card.get("set_name") or "Added by you").strip(),
                    official,
                    str(card.get("release_date") or "9999"),
                    str(card.get("image") or "").strip() or None,
                    now_iso(),
                ),
            )
            c.executemany(
                "INSERT INTO items (id, card_id, variant) VALUES (?, ?, ?)",
                [(f"{card_id}:{v}", card_id, v) for v in variants],
            )
        return {"id": card_id}

    @app.get("/api/cards/{card_id}/photos")
    async def card_photos(request: Request, card_id: str) -> dict[str, Any]:
        """Picture choices for a card: the official images plus recent eBay photos."""
        e = engine(request)
        card = e.db.one("SELECT image, image_alt, custom FROM cards WHERE id=?", (card_id,))
        if not card:
            raise HTTPException(404, "Unknown card")
        official = []
        if card["image"]:
            official.append(card["image"] if card["custom"] else f"{card['image']}/high.webp")
        if card["image_alt"]:
            official.append(card["image_alt"])
        rows = e.db.query(
            """
            SELECT l.images_json, l.image FROM listings l JOIN items i ON i.id = l.match_item_id
            WHERE i.card_id = ? AND COALESCE(l.photo_verdict, '') NOT IN
                ('fake', 'not_a_card', 'wrong_card')
            ORDER BY l.last_seen DESC LIMIT 6
            """,
            (card_id,),
        )
        ebay: list[str] = []
        for row in rows:
            for url in json.loads(row["images_json"] or "[]") or [row["image"]]:
                if url and url not in ebay:
                    ebay.append(url)
        return {"official": official, "ebay": ebay[:12]}

    @app.post("/api/cards/{card_id}/image")
    async def choose_image(
        request: Request, card_id: str, body: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        """Pick the picture shown for a card (empty to go back to the default)."""
        e = engine(request)
        url = str(body.get("url") or "").strip() or None
        if url and not url.startswith("https://"):
            raise HTTPException(400, "Picture links must start with https://")
        with e.db.tx() as c:
            updated = c.execute(
                "UPDATE cards SET image_override=? WHERE id=?", (url, card_id)
            ).rowcount
        if not updated:
            raise HTTPException(404, "Unknown card")
        return {"image_override": url}

    @app.delete("/api/cards/custom/{card_id}")
    async def delete_card(request: Request, card_id: str) -> dict[str, bool]:
        e = engine(request)
        if not e.db.one("SELECT id FROM cards WHERE id=? AND custom=1", (card_id,)):
            raise HTTPException(404, "Only cards you added can be removed")
        with e.db.tx() as c:
            c.execute(
                "DELETE FROM listings WHERE match_item_id IN"
                " (SELECT id FROM items WHERE card_id=?)",
                (card_id,),
            )
            c.execute("DELETE FROM items WHERE card_id=?", (card_id,))
            c.execute("DELETE FROM cards WHERE id=?", (card_id,))
        return {"ok": True}

    @app.post("/api/photo-check-key")
    async def set_photo_key(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Store (or clear, with an empty value) the Anthropic API key. It is never sent back."""
        e = engine(request)
        key = str(body.get("key") or "").strip()
        if key and not key.startswith("sk-ant-"):
            raise HTTPException(400, "That doesn't look like an Anthropic API key (sk-ant-...)")
        e.db.set_kv("anthropic_api_key", key or None)
        return {"photo_check_ready": e.photos.ready}

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
        if "owned" in changes:  # remembered for the "Recently added" list
            sets.append("owned_at=?")
            values.append(now_iso() if changes["owned"] else None)
        if sets:
            with e.db.tx() as c:
                c.execute(f"UPDATE items SET {', '.join(sets)} WHERE id=?", (*values, item_id))
        if changes.get("priority"):
            e.wake.set()  # search a newly starred card straight away
        return e.db.one("SELECT * FROM items WHERE id=?", (item_id,)) or {}

    @app.post("/api/listings/{listing_id}/fake")
    async def report_fake(
        request: Request, listing_id: str, body: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        e = engine(request)
        try:
            report_id = record_fake(e.db, listing_id, str(body.get("reason") or ""))
        except KeyError as exc:
            raise HTTPException(404, "Unknown listing") from exc
        with e.db.tx() as c:
            c.execute("DELETE FROM basket WHERE listing_id = ?", (listing_id,))
        await asyncio.to_thread(e.scanner.refresh_flags)
        return {"id": report_id, **summary(e.db, e.scanner.signals)}

    @app.get("/api/learning")
    async def learning_summary(request: Request) -> dict[str, Any]:
        e = engine(request)
        e.scanner.signals = learn(e.db, block_sellers=e.db.prefs()["block_reported_sellers"])
        return summary(e.db, e.scanner.signals)

    @app.post("/api/learning/undo/{report_id}")
    async def learning_undo(request: Request, report_id: int) -> dict[str, Any]:
        e = engine(request)
        try:
            undo_fake(e.db, report_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown report") from exc
        await asyncio.to_thread(e.scanner.refresh_flags)
        return summary(e.db, e.scanner.signals)

    @app.post("/api/learning/ignore-word")
    async def learning_ignore(request: Request, body: dict[str, Any] = Body(...)) -> dict:
        e = engine(request)
        word = str(body.get("word") or "").strip().lower()
        ignored = set(e.db.get_kv("ignored_fake_words", []) or [])
        if body.get("restore"):
            ignored.discard(word)
        elif word:
            ignored.add(word)
        e.db.set_kv("ignored_fake_words", sorted(ignored))
        await asyncio.to_thread(e.scanner.refresh_flags)
        return summary(e.db, e.scanner.signals)

    def basket_rows(e: Engine) -> list[dict[str, Any]]:
        return labelled(
            e.db.query(
                """
                SELECT b.added_at, l.item_id AS listing_id, l.title, l.url, l.image, l.price,
                       l.shipping, l.shipping_known, l.total, l.condition, l.seller,
                       l.fair_gbp, l.last_seen, l.sold, l.checked_at, i.id AS match_item_id,
                       i.variant,
                       c.name AS card_name, c.set_name, c.local_id, c.image AS card_image,
                       c.image_alt, c.custom, c.image_override
                FROM basket b
                JOIN listings l ON l.item_id = b.listing_id
                LEFT JOIN items i ON i.id = l.match_item_id
                LEFT JOIN cards c ON c.id = i.card_id
                ORDER BY b.added_at
                """
            )
        )

    @app.get("/api/basket")
    async def basket(request: Request) -> dict[str, Any]:
        return {"items": basket_rows(engine(request))}

    @app.post("/api/basket")
    async def basket_add(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        e = engine(request)
        ids = body.get("listing_ids") or [body.get("listing_id")]
        with e.db.tx() as c:
            for listing_id in filter(None, ids):
                if c.execute("SELECT 1 FROM listings WHERE item_id=?", (listing_id,)).fetchone():
                    c.execute(
                        "INSERT OR IGNORE INTO basket (listing_id, added_at) VALUES (?, ?)",
                        (listing_id, now_iso()),
                    )
        return {"items": basket_rows(e)}

    @app.delete("/api/basket/{listing_id}")
    async def basket_remove(request: Request, listing_id: str) -> dict[str, Any]:
        e = engine(request)
        with e.db.tx() as c:
            if listing_id == "all":
                c.execute("DELETE FROM basket")
            else:
                c.execute("DELETE FROM basket WHERE listing_id=?", (listing_id,))
        return {"items": basket_rows(e)}

    @app.post("/api/basket/bought")
    async def basket_bought(request: Request) -> dict[str, Any]:
        """We bought everything in the basket: tick the cards off and empty it."""
        e = engine(request)
        newly_owned = [
            row["id"]
            for row in e.db.query(
                "SELECT DISTINCT i.id FROM basket b JOIN listings l ON l.item_id = b.listing_id"
                " JOIN items i ON i.id = l.match_item_id WHERE i.owned = 0"
            )
        ]
        with e.db.tx() as c:
            c.executemany(
                "UPDATE items SET owned = 1, owned_at = ? WHERE id = ?",
                [(now_iso(), item_id) for item_id in newly_owned],
            )
            c.execute(
                "UPDATE listings SET dismissed = 1 WHERE item_id IN (SELECT listing_id FROM basket)"
            )
            c.execute("DELETE FROM basket")
        return {"items": [], "newly_owned": newly_owned}

    @app.post("/api/items-bulk")
    async def update_items(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, int]:
        """Mark several card versions owned or not owned at once (used by Undo)."""
        e = engine(request)
        ids = [str(i) for i in body.get("ids") or []]
        owned = bool(body.get("owned"))
        with e.db.tx() as c:
            changed = c.executemany(
                "UPDATE items SET owned = ?, owned_at = ? WHERE id = ?",
                [(int(owned), now_iso() if owned else None, item_id) for item_id in ids],
            ).rowcount
        return {"changed": changed}

    @app.post("/api/listings/{listing_id}/dismiss")
    async def dismiss(request: Request, listing_id: str) -> dict[str, bool]:
        with engine(request).db.tx() as c:
            c.execute("UPDATE listings SET dismissed=1 WHERE item_id=?", (listing_id,))
        return {"ok": True}

    @app.get("/api/filters")
    async def get_filters(request: Request) -> dict[str, Any]:
        return clean(engine(request).db.get_kv("deal_filters", {}) or {})

    @app.post("/api/filters")
    async def save_filters(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Save the Deals tab filters (and whether they also decide Telegram alerts)."""
        e = engine(request)
        saved = clean(body)
        e.db.set_kv("deal_filters", saved)
        return saved

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
        await asyncio.to_thread(e.scanner.refresh_flags)  # apply the new rules straight away
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
