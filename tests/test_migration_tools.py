from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


def load_script(filename: str, module_name: str):
    path = Path(__file__).parents[1] / "deploy" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_reads_fixed_legacy_installation_and_never_copies_secrets(tmp_path):
    migration = load_script("migrate-config.py", "migrate_config")
    snapshot = load_script("prepare-legacy-migration.py", "prepare_legacy_migration")

    assert Path("/home/commandadmin/pokehunter/.env") == migration.LEGACY_ENV
    assert Path("/etc/pokehunter.env") == migration.OUTPUT
    assert Path("/home/commandadmin/pokehunter") == snapshot.LEGACY_ROOT
    assert Path("/var/backups/pokehunter") == snapshot.BACKUP_ROOT

    env_file = tmp_path / ".env"
    env_file.write_text("EBAY_CLIENT_ID=client\nEBAY_CLIENT_SECRET=private\n")
    env_file.chmod(0o600)
    assert migration.parse_env(migration.secure_read(env_file))["EBAY_CLIENT_SECRET"] == "private"

    source = (Path(__file__).parents[1] / "deploy" / "prepare-legacy-migration.py").read_text()
    assert '"credentials_copied": False' in source
    assert '"personal_data_copied": False' in source
    assert "legacy_env_metadata" in source
    assert "hash_contents=True\n    )\n    entrypoint_metadata" not in source


def test_migration_rejects_symlink_source(tmp_path):
    migration = load_script("migrate-config.py", "migrate_config_symlink")
    target = tmp_path / "target.env"
    target.write_text("EBAY_CLIENT_ID=client\n")
    target.chmod(0o600)
    link = tmp_path / ".env"
    link.symlink_to(target)

    with pytest.raises(OSError):
        migration.secure_read(link)


def test_migration_source_must_be_private(tmp_path):
    migration = load_script("migrate-config.py", "migrate_config_mode")
    env_file = tmp_path / ".env"
    env_file.write_text("EBAY_CLIENT_ID=client\n")
    env_file.chmod(0o640)

    with pytest.raises(ValueError, match="mode 0600"):
        migration.secure_read(env_file)


def test_snapshot_manifest_excludes_env_contents_and_database_hash(tmp_path, monkeypatch):
    snapshot = load_script("prepare-legacy-migration.py", "prepare_legacy_manifest")
    legacy = tmp_path / "legacy"
    (legacy / "app").mkdir(parents=True)
    (legacy / "data").mkdir()
    env_file = legacy / ".env"
    env_file.write_text("SECRET=never-copy\n")
    env_file.chmod(0o600)
    entrypoint = legacy / "app" / "ebay_notifications.py"
    entrypoint.write_text("print('legacy')\n")
    database = legacy / "data" / "ebay_deletions.sqlite3"
    database.write_bytes(b"personal-data-placeholder")
    unit = tmp_path / "pokehunter-ebay.service"
    unit.write_text(f"ExecStart=/usr/bin/python3 {entrypoint}\n")
    backup = tmp_path / "backups"
    backup.mkdir(mode=0o700)

    monkeypatch.setattr(snapshot, "LEGACY_ROOT", legacy)
    monkeypatch.setattr(snapshot, "LEGACY_ENV", env_file)
    monkeypatch.setattr(snapshot, "LEGACY_ENTRYPOINT", entrypoint)
    monkeypatch.setattr(snapshot, "LEGACY_DATABASE", database)
    monkeypatch.setattr(snapshot, "LEGACY_UNIT", unit)
    monkeypatch.setattr(snapshot, "BACKUP_ROOT", backup)
    monkeypatch.setattr(snapshot, "commandadmin_uid", lambda: os.getuid())
    monkeypatch.setattr(snapshot, "require_root", lambda: None)
    monkeypatch.setenv("POKEHUNTER_MIGRATION_PREP_APPROVED", "YES")

    destination = snapshot.prepare_snapshot()
    manifest = (destination / "manifest.json").read_text()
    assert "never-copy" not in manifest
    assert "personal-data-placeholder" not in manifest
    assert '"credentials_copied": false' in manifest
    assert '"personal_data_copied": false' in manifest
    assert not (destination / ".env").exists()
    assert not (destination / "ebay_deletions.sqlite3").exists()
    assert (destination / "pokehunter-ebay.service").is_file()
