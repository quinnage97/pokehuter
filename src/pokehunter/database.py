from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Listing


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS listing_observations (
                    item_id TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    latest_total_price TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    alert_attempts INTEGER NOT NULL DEFAULT 0,
                    alert_sent_at TEXT,
                    last_alert_error TEXT
                );

                CREATE TABLE IF NOT EXISTS monitor_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    discovered_count INTEGER NOT NULL DEFAULT 0,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    alert_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def observe(self, listing: Listing) -> bool:
        """Upsert an observation and return whether an alert has already succeeded."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO listing_observations (
                    item_id, first_seen_at, last_seen_at, latest_total_price, currency
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    latest_total_price=excluded.latest_total_price,
                    currency=excluded.currency
                """,
                (listing.item_id, now, now, str(listing.total_price), listing.currency),
            )
            row = connection.execute(
                "SELECT alert_sent_at FROM listing_observations WHERE item_id=?",
                (listing.item_id,),
            ).fetchone()
        return bool(row and row["alert_sent_at"])

    def mark_alert_attempt(self, item_id: str, *, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE listing_observations
                SET alert_attempts=alert_attempts + 1, last_alert_error=?
                WHERE item_id=?
                """,
                ((error or "")[:500] or None, item_id),
            )

    def mark_alert_sent(self, item_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE listing_observations
                SET alert_sent_at=?, last_alert_error=NULL
                WHERE item_id=? AND alert_sent_at IS NULL
                """,
                (utc_now(), item_id),
            )

    def start_run(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO monitor_runs (started_at) VALUES (?)",
                (utc_now(),),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        discovered: int,
        candidates: int,
        alerts: int,
        errors: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE monitor_runs SET
                    finished_at=?, discovered_count=?, candidate_count=?,
                    alert_count=?, error_count=?
                WHERE id=?
                """,
                (utc_now(), discovered, candidates, alerts, errors, run_id),
            )

    def prune(self, retention_days: int = 180) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM listing_observations WHERE last_seen_at < ?",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM monitor_runs WHERE started_at < ?",
                (cutoff,),
            )
            return cursor.rowcount
