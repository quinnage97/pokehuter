from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


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
            "CREATE TABLE deletions (notification_id TEXT, username TEXT, user_id TEXT, "
            "received_at TEXT)"
        )
        connection.execute(
            "INSERT INTO deletions VALUES (?, ?, ?, ?)",
            ("notification", "private-username", "private-user-id", "2026-01-01"),
        )
        connection.commit()
    finally:
        connection.close()

    purge.purge_database(database)

    assert not database.exists()
    assert not database.with_name(database.name + "-wal").exists()
    assert not database.with_name(database.name + "-shm").exists()


def test_legacy_purge_refuses_database_with_unrelated_table(tmp_path):
    purge = load_purge()
    database = tmp_path / "ebay_deletions.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE deletions (notification_id TEXT, username TEXT, user_id TEXT, "
            "received_at TEXT)"
        )
        connection.execute("CREATE TABLE unrelated_business_data (value TEXT)")
        connection.execute("INSERT INTO unrelated_business_data VALUES ('preserve-me')")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="unrelated tables"):
        purge.purge_database(database)

    assert database.exists()
    connection = sqlite3.connect(database)
    try:
        value = connection.execute("SELECT value FROM unrelated_business_data").fetchone()
    finally:
        connection.close()
    assert value == ("preserve-me",)
