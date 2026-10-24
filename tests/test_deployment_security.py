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
    assert "NOPASSWD: /usr/local/sbin/pokehunter-control *" in bootstrap
    assert "usermod -aG sudo" not in bootstrap


def test_privileged_deploy_uses_only_root_owned_helpers_and_units():
    remote = (ROOT / "deploy" / "remote-deploy.sh").read_text()
    assert '"${release_dir}/deploy/verify-release.sh"' not in remote
    assert '"${release_dir}/deploy/purge-legacy-deletion-db.py"' not in remote
    assert '"${release_dir}/deploy/systemd/' not in remote
    assert '"${LIBEXEC}/verify-release.sh"' in remote
    assert '"${LIBEXEC}/purge-legacy-deletion-db.py"' in remote
    assert '"${LIBEXEC}/validate-release.py"' in remote
    assert 'ROOT_ARTIFACT="/var/cache/pokehunter/staging/' in remote
