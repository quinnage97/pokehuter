#!/usr/bin/python3 -I
"""Validate an uploaded PokéHunter release archive without extracting it."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
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
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("release archive is not a regular file")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise ValueError("release archive must be controlled by the effective user")
        if metadata.st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("release archive is too large")

        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise ValueError("release artifact checksum mismatch")

        source.seek(0)
        names: set[str] = set()
        expanded_bytes = 0
        with tarfile.open(fileobj=source, mode="r:gz") as archive:
            for count, member in enumerate(archive, start=1):
                if count > MAX_MEMBERS:
                    raise ValueError("release archive has too many members")
                member_path = PurePosixPath(member.name)
                normalized_name = member.name.rstrip("/")
                if (
                    not normalized_name
                    or member_path.is_absolute()
                    or "." in member_path.parts
                    or ".." in member_path.parts
                    or normalized_name in names
                ):
                    raise ValueError("release archive contains an unsafe or duplicate path")
                if not (member.isfile() or member.isdir()):
                    raise ValueError("release archive contains a link or special file")
                expanded_bytes += member.size
                if expanded_bytes > MAX_EXPANDED_BYTES:
                    raise ValueError("expanded release archive is too large")
                names.add(normalized_name)

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
