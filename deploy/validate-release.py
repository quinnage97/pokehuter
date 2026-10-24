#!/usr/bin/env python3
"""Validate an uploaded PokéHunter release archive without extracting it."""

from __future__ import annotations

import argparse
import hashlib
import re
import tarfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 250 * 1024 * 1024
MAX_MEMBERS = 5_000
REQUIRED_MEMBERS = {
    "pyproject.toml",
    "src/pokehunter/app.py",
    "deploy/constraints.txt",
}


def validate_archive(path: Path, release_id: str, expected_sha256: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{7,40}", release_id):
        raise ValueError("invalid release identifier")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("invalid release checksum")
    if not path.is_file() or path.is_symlink():
        raise ValueError("release archive is not a regular file")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("release archive is too large")

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("release artifact checksum mismatch")

    names: set[str] = set()
    expanded_bytes = 0
    with tarfile.open(path, mode="r:gz") as archive:
        for count, member in enumerate(archive, start=1):
            if count > MAX_MEMBERS:
                raise ValueError("release archive has too many members")
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("release archive contains an unsafe path")
            if not (member.isfile() or member.isdir()):
                raise ValueError("release archive contains a link or special file")
            expanded_bytes += member.size
            if expanded_bytes > MAX_EXPANDED_BYTES:
                raise ValueError("expanded release archive is too large")
            names.add(member.name.rstrip("/"))

    if REQUIRED_MEMBERS - names:
        raise ValueError("release archive is missing required project files")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("release_id")
    parser.add_argument("sha256")
    args = parser.parse_args()
    try:
        validate_archive(args.archive, args.release_id, args.sha256)
    except (OSError, tarfile.TarError, ValueError) as exc:
        print(f"release_validation=failed reason={exc}")
        return 1
    print("release_validation=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
