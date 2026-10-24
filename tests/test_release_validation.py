from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest


def load_validator():
    path = Path(__file__).parents[1] / "deploy" / "validate-release.py"
    spec = importlib.util.spec_from_file_location("validate_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_archive(path: Path, *, extra: tarfile.TarInfo | None = None) -> str:
    with tarfile.open(path, "w:gz") as archive:
        for name in ("pyproject.toml", "src/pokehunter/app.py", "deploy/constraints.txt"):
            content = b"safe"
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        if extra:
            archive.addfile(extra)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_valid_release_archive_passes(tmp_path):
    validator = load_validator()
    archive = tmp_path / "release.tar.gz"
    digest = build_archive(archive)

    validator.validate_archive(archive, "abcdef123456", digest)


def test_release_archive_rejects_symlink(tmp_path):
    validator = load_validator()
    archive = tmp_path / "release.tar.gz"
    link = tarfile.TarInfo("escape")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc"
    digest = build_archive(archive, extra=link)

    with pytest.raises(ValueError, match="link or special"):
        validator.validate_archive(archive, "abcdef123456", digest)


def test_release_archive_rejects_checksum_mismatch(tmp_path):
    validator = load_validator()
    archive = tmp_path / "release.tar.gz"
    build_archive(archive)

    with pytest.raises(ValueError, match="checksum mismatch"):
        validator.validate_archive(archive, "abcdef123456", "0" * 64)
