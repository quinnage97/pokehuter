from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def load_launcher():
    path = Path(__file__).parents[1] / "deploy" / "run-release-check.py"
    spec = importlib.util.spec_from_file_location("run_release_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_parser_supports_migrated_quoting_without_execution(tmp_path):
    launcher = load_launcher()
    marker = tmp_path / "must-not-exist"
    config = tmp_path / "pokehunter.env"
    config.write_text(
        "EBAY_CLIENT_ID='client id'\n"
        f"EBAY_CLIENT_SECRET='$(touch {marker})'\n"
        "EBAY_DELIVERY_POSTCODE='SW1A 1AA'\n"
    )
    config.chmod(0o600)

    values = launcher.load_config(config)

    assert values["EBAY_CLIENT_ID"] == "client id"
    assert values["EBAY_CLIENT_SECRET"] == f"$(touch {marker})"
    assert not marker.exists()


def test_config_parser_rejects_environment_injection_key(tmp_path):
    launcher = load_launcher()
    config = tmp_path / "pokehunter.env"
    config.write_text("LD_PRELOAD=/tmp/attacker.so\n")
    config.chmod(0o600)

    with pytest.raises(ValueError, match="unsupported configuration key"):
        launcher.load_config(config)


def test_config_parser_rejects_symlink(tmp_path):
    launcher = load_launcher()
    target = tmp_path / "target.env"
    target.write_text("EBAY_CLIENT_ID=client\n")
    target.chmod(0o600)
    symlink = tmp_path / "pokehunter.env"
    symlink.symlink_to(target)

    with pytest.raises(OSError):
        launcher.load_config(symlink)
