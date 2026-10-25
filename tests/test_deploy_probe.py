from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load_probe():
    path = Path(__file__).parents[1] / "deploy" / "probe-existing-ebay.py"
    spec = importlib.util.spec_from_file_location("probe_existing_ebay", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_ebay_probe_reports_only_safe_metadata(tmp_path, monkeypatch, capsys):
    probe = load_probe()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EBAY_CLIENT_ID='client-id'\n"
        "EBAY_CLIENT_SECRET='never-print-this'\n"
        "EBAY_AFFILIATE_CAMPAIGN_ID=1234567890\n"
        "EBAY_DELIVERY_POSTCODE='SW1A 1AA'\n"
    )
    env_file.chmod(0o600)
    responses = iter(
        [
            (200, {"access_token": "never-print-token"}),
            (200, {"itemSummaries": [{"title": "never-print-listing"}]}),
        ]
    )
    monkeypatch.setattr(probe, "request_json", lambda request: next(responses))
    monkeypatch.setattr(sys, "argv", ["probe-existing-ebay.py", "--env", str(env_file)])
    for key in (
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "EBAY_APP_ID",
        "EBAY_CERT_ID",
        "EBAY_CLIENTID",
        "EBAY_CLIENTSECRET",
        "EBAY_AFFILIATE_CAMPAIGN_ID",
        "EBAY_DELIVERY_POSTCODE",
    ):
        monkeypatch.delenv(key, raising=False)

    assert probe.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "status": "ok",
        "oauth_http": 200,
        "browse_http": 200,
        "items_returned": 1,
        "affiliate_configured": True,
        "delivery_postcode_configured": True,
    }
    assert "never-print" not in output


def test_existing_ebay_probe_fails_closed_without_production_context(tmp_path, monkeypatch, capsys):
    probe = load_probe()
    env_file = tmp_path / ".env"
    env_file.write_text("EBAY_CLIENT_ID=client-id\nEBAY_CLIENT_SECRET=client-secret\n")
    env_file.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["probe-existing-ebay.py", "--env", str(env_file)])
    for key in (
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "EBAY_AFFILIATE_CAMPAIGN_ID",
        "EBAY_DELIVERY_POSTCODE",
    ):
        monkeypatch.delenv(key, raising=False)

    assert probe.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "stage": "configuration",
        "affiliate_or_postcode_invalid": True,
    }


def test_existing_ebay_probe_ignores_inherited_credentials(tmp_path, monkeypatch, capsys):
    probe = load_probe()
    env_file = tmp_path / ".env"
    env_file.write_text("EBAY_CLIENT_ID=file-client\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "inherited-secret-must-not-be-used")
    monkeypatch.setattr(sys, "argv", ["probe-existing-ebay.py", "--env", str(env_file)])

    assert probe.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "stage": "configuration",
        "missing_credentials": True,
    }


def test_existing_ebay_probe_rejects_symlink_environment(tmp_path):
    probe = load_probe()
    target = tmp_path / "target.env"
    target.write_text("EBAY_CLIENT_ID=client\n")
    symlink = tmp_path / "linked.env"
    symlink.symlink_to(target)

    with pytest.raises(OSError):
        probe.read_env(symlink)
