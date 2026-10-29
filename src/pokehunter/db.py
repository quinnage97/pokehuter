from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    pokemon TEXT NOT NULL,
    name TEXT NOT NULL,
    local_id TEXT NOT NULL,
    set_id TEXT NOT NULL,
    set_name TEXT NOT NULL,
    set_official INTEGER,
    release_date TEXT,
    serie_name TEXT,
    rarity TEXT,
    image TEXT,
    pricing_json TEXT,
    updated_at TEXT NOT NULL
);

-- One checklist entry per card *variant* (normal / holo / reverse / 1st edition): a master
-- set needs every one of them.
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id),
    variant TEXT NOT NULL,
    owned INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    target_price REAL,
    market_gbp REAL,
    price_source TEXT,
    last_searched_at TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    item_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    image TEXT,
    price REAL NOT NULL,
    shipping REAL NOT NULL,
    shipping_known INTEGER NOT NULL DEFAULT 1,
    total REAL NOT NULL,
    condition TEXT NOT NULL,
    seller TEXT,
    feedback_score INTEGER,
    feedback_pct REAL,
    listed_at TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    match_item_id TEXT,
    market_gbp REAL,
    fair_gbp REAL,
    discount_pct REAL,
    is_deal INTEGER NOT NULL DEFAULT 0,
    warning TEXT,
    alert_status TEXT,
    alerted_at TEXT,
    dismissed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS listings_deals ON listings(is_deal, last_seen);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ebay_calls INTEGER NOT NULL DEFAULT 0,
    listings_seen INTEGER NOT NULL DEFAULT 0,
    deals INTEGER NOT NULL DEFAULT 0,
    alerts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS fake_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    item_id TEXT,
    title TEXT NOT NULL,
    seller TEXT,
    total REAL,
    fair_gbp REAL,
    reason TEXT NOT NULL,
    image TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS basket (listing_id TEXT PRIMARY KEY, added_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS api_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Columns added after the first release, applied to existing databases on start-up.
MIGRATIONS = [
    ("items", "ebay_median_gbp", "REAL"),
    ("items", "ebay_comps", "INTEGER"),
    ("cards", "image_alt", "TEXT"),
    ("cards", "custom", "INTEGER NOT NULL DEFAULT 0"),
    ("cards", "image_override", "TEXT"),
    ("listings", "images_json", "TEXT"),
    ("listings", "photo_verdict", "TEXT"),
    ("listings", "photo_reason", "TEXT"),
    ("listings", "user_verdict", "TEXT"),
    ("items", "owned_at", "TEXT"),
    ("listings", "sold", "INTEGER NOT NULL DEFAULT 0"),
    ("listings", "checked_at", "TEXT"),
    ("listings", "reseller_alerted_at", "TEXT"),
    ("cards", "collect", "INTEGER NOT NULL DEFAULT 1"),
]

# Defaults for the options shown on the website's Settings tab.
DEFAULT_PREFS: dict[str, Any] = {
    # Alert when a listing is between these two % below the market price; much cheaper
    # usually means a damaged copy.
    "min_discount_pct": 20,
    "max_discount_pct": 30,
    "priority_discount_pct": 5,  # looser lower limit for starred cards
    "discount_basis": "market",  # % off the Near Mint market price, or "condition"-adjusted
    "allowed_conditions": ["NM", "LP", "MP", "?"],
    "min_saving_gbp": 2,
    # Far cheaper than this is almost never the real card (fakes, stickers, lucky dips).
    "suspicious_above_pct": 50,
    "min_seller_feedback": 5,
    "trusted_seller_above_gbp": 30,  # cards worth this much...
    "trusted_seller_feedback": 50,  # ...need a seller with at least this much feedback
    "uk_sellers_only": True,
    "english_only": True,  # only listings eBay has marked as English cards
    "photo_check_enabled": True,
    "photo_checks_per_day": 20,  # caps what the photo check can cost in a day
    "photo_check_model": "best",  # "best" (Opus) or "cheap" (Haiku)
    "block_reported_sellers": True,
    # Reseller tab: listings priced at 70-90% of market value, with a profit estimate.
    "reseller_min_of_market": 70,
    "reseller_max_of_market": 90,
    "reseller_profit_only": True,
    "reseller_fee_pct": 13,  # eBay UK business seller fees (private sellers pay none)
    "reseller_postage_gbp": 2,  # what posting a sold card costs you
    "reseller_alerts": False,
    "reseller_sweeps_per_scan": 8,  # extra Pokémon searched per scan, in rotation
    "alerts_enabled": True,
    "max_alerts_per_scan": 10,
    "targeted_per_scan": 30,
    "price_source": "lowest",  # or "cardmarket" / "tcgplayer"
}


# Allowed values for the text settings.
CHOICES = {
    "price_source": ("lowest", "cardmarket", "tcgplayer"),
    "photo_check_model": ("best", "cheap"),
    "discount_basis": ("market", "condition"),
}
CONDITIONS = ("NM", "LP", "MP", "HP", "DMG", "?")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        for table, name, kind in MIGRATIONS:
            columns = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if name not in columns:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        self._conn.close()

    # key/value store -------------------------------------------------------------------
    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def prefs(self) -> dict[str, Any]:
        stored = self.get_kv("prefs", {}) or {}
        # Moderately Played was added to the defaults later; bring settings saved with the
        # old default list along (a list someone chose themselves is left alone).
        if stored.get("allowed_conditions") == ["NM", "LP", "?"]:
            stored["allowed_conditions"] = ["NM", "LP", "MP", "?"]
        return {**DEFAULT_PREFS, **{k: v for k, v in stored.items() if k in DEFAULT_PREFS}}

    def update_prefs(self, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.prefs()
        for key, value in changes.items():
            if key not in DEFAULT_PREFS:
                continue
            default = DEFAULT_PREFS[key]
            if isinstance(default, bool):
                current[key] = bool(value)
            elif key == "allowed_conditions":
                current[key] = [c for c in CONDITIONS if c in (value or [])]
            elif isinstance(default, int):
                current[key] = max(0, int(float(value)))
            elif value in CHOICES.get(key, ()):
                current[key] = value
        self.set_kv("prefs", current)
        return current

    # eBay call budget ------------------------------------------------------------------
    def calls_today(self) -> int:
        row = self.one("SELECT calls FROM api_usage WHERE day=?", (now_iso()[:10],))
        return row["calls"] if row else 0

    def count_call(self) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO api_usage(day, calls) VALUES(?, 1) "
                "ON CONFLICT(day) DO UPDATE SET calls=calls+1",
                (now_iso()[:10],),
            )
