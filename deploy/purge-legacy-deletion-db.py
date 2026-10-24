#!/usr/bin/env python3
"""Secure-delete rows, compact, and unlink the exact legacy deletion database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

LEGACY_DATABASE = Path("/opt/pokehunter/data/ebay_deletions.sqlite3")
EXPECTED_COLUMNS = {"notification_id", "username", "user_id", "received_at"}


def purge_database(path: Path) -> None:
    if not path.exists():
        return
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA secure_delete=ON")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != {"deletions"}:
            raise RuntimeError(
                "refusing legacy purge: database contains missing or unrelated tables"
            )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(deletions)")}
        if columns != EXPECTED_COLUMNS:
            raise RuntimeError("refusing legacy purge: unexpected deletions schema")
        connection.execute("DELETE FROM deletions")
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()

    for candidate in (
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
        path,
    ):
        candidate.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main() -> int:
    if os.geteuid() != 0:
        print("legacy database purge must run as root")
        return 1
    purge_database(LEGACY_DATABASE)
    print("legacy_deletion_database=purged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
