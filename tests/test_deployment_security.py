from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_client_never_streams_a_root_shell():
    client = (ROOT / "deploy" / "hetzner-deploy.sh").read_text()
    assert "sudo -n bash" not in client
    assert "sudo -n python3" not in client
    assert "sudo -n ${CONTROL_COMMAND}" in client


def test_bootstrap_grants_only_fixed_control_command():
    bootstrap = (ROOT / "deploy" / "bootstrap-root.sh").read_text()
    assert "NOPASSWD: ALL" not in bootstrap
    assert "NOPASSWD: /usr/local/sbin/pokehunter-control *" not in bootstrap
    assert "NOPASSWD:NOSETENV: /usr/local/sbin/pokehunter-control preflight" in bootstrap
    assert "NOPASSWD:NOSETENV: /usr/local/sbin/pokehunter-control probe-ebay" in bootstrap
    assert "usermod -aG sudo" not in bootstrap
    assert "systemctl daemon-reload" not in bootstrap
    assert "require_root_regular" in bootstrap
    assert "require_regular_target_or_absent" in bootstrap
    assert 'require_regular_target_or_absent "${destination}"' in bootstrap
    assert 'require_root_regular "${source}"' in bootstrap


def test_passwordless_dispatcher_replaces_environment_and_checks_helpers():
    control = (ROOT / "deploy" / "pokehunter-control").read_text()
    assert control.startswith("#!/usr/bin/python3 -I\n")
    assert '"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"' in control
    assert "os.execve(executable, argv, SAFE_ENV)" in control
    assert "lstat()" in control
    assert "target.st_uid != 0" in control
    assert "target.st_mode & 0o022" in control


def test_privileged_path_never_invokes_docker():
    privileged_files = [
        "bootstrap-root.sh",
        "pokehunter-control",
        "remote-deploy.sh",
        "server-preflight.sh",
    ]
    for filename in privileged_files:
        content = (ROOT / "deploy" / filename).read_text()
        assert "docker ps" not in content
        assert "docker exec" not in content


def test_privileged_deploy_uses_only_root_owned_helpers_and_units():
    remote = (ROOT / "deploy" / "remote-deploy.sh").read_text()
    assert '"${release_dir}/deploy/verify-release.sh"' not in remote
    assert '"${release_dir}/deploy/purge-legacy-deletion-db.py"' not in remote
    assert '"${release_dir}/deploy/systemd/' not in remote
    assert '"${LIBEXEC}/verify-release.sh"' in remote
    assert '"${LIBEXEC}/purge-legacy-deletion-db.py"' in remote
    assert '"${LIBEXEC}/validate-release.py"' in remote
    assert '"${LIBEXEC}/run-release-check.py"' in remote
    assert 'ROOT_ARTIFACT="/var/cache/pokehunter/staging/' in remote
    assert 'RELEASE_SUDOERS="/etc/sudoers.d/pokehunter-release"' in remote
    assert 'rm -f "${RELEASE_SUDOERS}"' in remote


def test_release_authorization_is_exact_and_separately_gated():
    authorizer = (ROOT / "deploy" / "authorize-release-root.sh").read_text()
    assert 'POKEHUNTER_RELEASE_APPROVED:-}" != "YES"' in authorizer
    assert "pokehunter-control *" not in authorizer
    assert "NOPASSWD:NOSETENV: %s apply %s %s %s %s %s %s" in authorizer


def test_privileged_shells_use_fixed_interpreter_and_urls():
    for filename in (
        "authorize-release-root.sh",
        "bootstrap-root.sh",
        "remote-deploy.sh",
        "server-preflight.sh",
        "verify-release.sh",
    ):
        assert (ROOT / "deploy" / filename).read_text().startswith("#!/bin/bash\n")
    preflight = (ROOT / "deploy" / "server-preflight.sh").read_text()
    verify = (ROOT / "deploy" / "verify-release.sh").read_text()
    assert "${POKEHUNTER_PUBLIC_URL" not in preflight
    assert "${POKEHUNTER_PORT" not in preflight
    assert "${POKEHUNTER_PUBLIC_URL" not in verify


def test_release_config_is_never_sourced_as_shell_code():
    remote = (ROOT / "deploy" / "remote-deploy.sh").read_text()
    assert "source /etc/pokehunter.env" not in remote
    assert "run-release-check.py" in remote


def test_webhook_service_does_not_receive_scanner_or_telegram_secrets():
    unit = (ROOT / "deploy" / "systemd" / "pokehunter-web.service").read_text()
    unset = " ".join(
        line.removeprefix("UnsetEnvironment=")
        for line in unit.splitlines()
        if line.startswith("UnsetEnvironment=")
    ).split()
    for key in (
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "EBAY_AFFILIATE_CAMPAIGN_ID",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DATABASE_PATH",
    ):
        assert key in unset


def test_ebay_probe_disables_proxies_redirects_and_environment_credentials():
    probe = (ROOT / "deploy" / "probe-existing-ebay.py").read_text()
    assert "ProxyHandler({})" in probe
    assert "NoRedirectHandler()" in probe
    assert "os.environ" not in probe
