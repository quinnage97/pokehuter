from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def load_purge():
    path = Path(__file__).parents[1] / "deploy" / "purge-legacy-deletion-db.py"
    spec = importlib.util.spec_from_file_location("purge_legacy_deletion_db", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_deletion_database_is_compacted_and_unlinked(tmp_path):
    purge = load_purge()
    database = tmp_path / "ebay_deletions.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE deletions (notification_id TEXT, username TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO deletions VALUES (?, ?, ?)",
            ("notification", "private-username", "private-user-id"),
        )
        connection.commit()
    finally:
        connection.close()

    purge.purge_database(database)

    assert not database.exists()
    assert not database.with_name(database.name + "-wal").exists()
    assert not database.with_name(database.name + "-shm").exists()
