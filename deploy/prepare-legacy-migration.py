#!/usr/bin/python3 -I
"""Create a root-only recovery manifest before migrating the legacy installation."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path

LEGACY_ROOT = Path("/home/commandadmin/pokehunter")
LEGACY_ENV = LEGACY_ROOT / ".env"
LEGACY_ENTRYPOINT = LEGACY_ROOT / "app/ebay_notifications.py"
LEGACY_DATABASE = LEGACY_ROOT / "data/ebay_deletions.sqlite3"
LEGACY_UNIT = Path("/etc/systemd/system/pokehunter-ebay.service")
BACKUP_ROOT = Path("/var/backups/pokehunter")
MAX_HASH_BYTES = 16 * 1024 * 1024


def commandadmin_uid() -> int:
    return pwd.getpwnam("commandadmin").pw_uid


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("migration snapshot must run as root")


def file_metadata(
    path: Path,
    *,
    allowed_owners: set[int],
    exact_mode: int | None = None,
    hash_contents: bool = False,
    allow_group_world_writable: bool = False,
) -> tuple[dict[str, int | str], bytes | None]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"not a regular file: {path}")
        if metadata.st_uid not in allowed_owners:
            raise ValueError(f"unexpected owner: {path}")
        mode = stat.S_IMODE(metadata.st_mode)
        if exact_mode is not None and mode != exact_mode:
            raise ValueError(f"unexpected mode: {path}")
        if not allow_group_world_writable and mode & 0o022:
            raise ValueError(f"writable migration source: {path}")
        contents = None
        digest = None
        if hash_contents:
            if metadata.st_size > MAX_HASH_BYTES:
                raise ValueError(f"file exceeds snapshot validation limit: {path}")
            chunks: list[bytes] = []
            remaining = MAX_HASH_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            contents = b"".join(chunks)
            if len(contents) > MAX_HASH_BYTES:
                raise ValueError(f"file exceeds snapshot validation limit: {path}")
            digest = hashlib.sha256(contents).hexdigest()
        result: dict[str, int | str] = {
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": f"{mode:04o}",
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
        }
        if digest is not None:
            result["sha256"] = digest
        return result, contents
    finally:
        os.close(descriptor)


def validate_directory(path: Path, allowed_owners: set[int]) -> dict[str, int | str]:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in allowed_owners:
        raise ValueError(f"unsafe legacy directory: {path}")
    if metadata.st_mode & 0o022:
        raise ValueError(f"writable legacy directory: {path}")
    return {
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


def prepare_snapshot() -> Path:
    require_root()
    if os.environ.get("POKEHUNTER_MIGRATION_PREP_APPROVED") != "YES":
        raise PermissionError("migration snapshot approval gate is closed")

    legacy_owners = {0, commandadmin_uid()}
    root_metadata = validate_directory(LEGACY_ROOT, legacy_owners)
    env_metadata, _ = file_metadata(LEGACY_ENV, allowed_owners=legacy_owners, exact_mode=0o600)
    entrypoint_metadata, _ = file_metadata(
        LEGACY_ENTRYPOINT, allowed_owners=legacy_owners, hash_contents=True
    )
    unit_metadata, unit_contents = file_metadata(
        LEGACY_UNIT, allowed_owners={0, os.geteuid()}, hash_contents=True
    )
    assert unit_contents is not None
    if str(LEGACY_ENTRYPOINT).encode() not in unit_contents:
        raise ValueError("legacy unit does not reference the expected entrypoint")

    database: dict[str, int | str | bool] = {"present": False}
    try:
        database_metadata, _ = file_metadata(
            LEGACY_DATABASE,
            allowed_owners=legacy_owners,
            allow_group_world_writable=True,
        )
    except FileNotFoundError:
        pass
    else:
        database = {"present": True, **database_metadata}

    backup_metadata = BACKUP_ROOT.lstat()
    if (
        not stat.S_ISDIR(backup_metadata.st_mode)
        or backup_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(backup_metadata.st_mode) != 0o700
    ):
        raise ValueError("backup root must be a root-owned directory with mode 0700")

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = BACKUP_ROOT / f"legacy-migration-{timestamp}-{os.getpid()}"
    os.mkdir(destination, 0o700)
    try:
        unit_copy = destination / "pokehunter-ebay.service"
        unit_descriptor = os.open(
            unit_copy,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(unit_descriptor, unit_contents)
            os.fsync(unit_descriptor)
        finally:
            os.close(unit_descriptor)

        manifest = {
            "created_utc": timestamp,
            "legacy_root": str(LEGACY_ROOT),
            "legacy_unit_name": "pokehunter-ebay.service",
            "legacy_root_metadata": root_metadata,
            # Credential contents and hashes are deliberately excluded. The source remains in place.
            "legacy_env_metadata": env_metadata,
            "legacy_entrypoint_metadata": entrypoint_metadata,
            "legacy_unit_metadata": unit_metadata,
            # Personal identifiers are deliberately not copied into the recovery area.
            "legacy_deletion_database_metadata": database,
            "credentials_copied": False,
            "personal_data_copied": False,
        }
        manifest_path = destination / "manifest.json"
        manifest_descriptor = os.open(
            manifest_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            os.write(manifest_descriptor, encoded)
            os.fsync(manifest_descriptor)
        finally:
            os.close(manifest_descriptor)
        directory_descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        shutil.rmtree(destination)
        raise
    return destination


def main() -> int:
    try:
        destination = prepare_snapshot()
    except (KeyError, OSError, ValueError) as exc:
        print(f"migration_snapshot_error={type(exc).__name__}")
        return 1
    print(f"migration_snapshot={destination}")
    print("credentials_copied=no personal_data_copied=no legacy_install_modified=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
