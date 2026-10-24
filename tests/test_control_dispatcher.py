from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest


def load_control():
    path = Path(__file__).parents[1] / "deploy" / "pokehunter-control"
    loader = importlib.machinery.SourceFileLoader("pokehunter_control", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_dispatcher_rejects_arguments_for_passwordless_preflight(monkeypatch):
    control = load_control()
    monkeypatch.setattr(control.os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["pokehunter-control", "preflight", "extra"])

    with pytest.raises(SystemExit) as exc:
        control.main()

    assert exc.value.code == 2


def test_dispatcher_replaces_inherited_environment(monkeypatch):
    control = load_control()
    captured = {}

    def fake_execve(executable, arguments, environment):
        captured.update(
            executable=executable,
            arguments=arguments,
            environment=environment,
        )
        raise RuntimeError("exec intercepted")

    monkeypatch.setenv("BASH_ENV", "/tmp/attacker")
    monkeypatch.setenv("POKEHUNTER_PUBLIC_URL", "https://attacker.invalid")
    monkeypatch.setattr(control.os, "execve", fake_execve)

    with pytest.raises(RuntimeError, match="exec intercepted"):
        control.execute("/trusted/helper.sh", [])

    assert captured == {
        "executable": "/bin/bash",
        "arguments": ["/bin/bash", "/trusted/helper.sh"],
        "environment": control.SAFE_ENV,
    }
    assert "BASH_ENV" not in captured["environment"]
    assert "POKEHUNTER_PUBLIC_URL" not in captured["environment"]


def test_probe_dispatch_has_fixed_path_and_arguments(monkeypatch):
    control = load_control()
    captured = {}
    monkeypatch.setattr(control.os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["pokehunter-control", "probe-ebay"])
    monkeypatch.setattr(control, "trusted_helper", lambda name: f"/trusted/{name}")

    def fake_execute(path, arguments, *, python=False):
        captured.update(path=path, arguments=arguments, python=python)
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(control, "execute", fake_execute)
    with pytest.raises(RuntimeError, match="exec intercepted"):
        control.main()

    assert captured == {
        "path": "/trusted/probe-existing-ebay.py",
        "arguments": ["--env", "/opt/pokehunter/.env"],
        "python": True,
    }
