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
    assert "NOPASSWD: /usr/local/sbin/pokehunter-control preflight" in bootstrap
    assert "NOPASSWD: /usr/local/sbin/pokehunter-control probe-ebay" in bootstrap
    assert "usermod -aG sudo" not in bootstrap
    assert "systemctl daemon-reload" not in bootstrap


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
    assert 'ROOT_ARTIFACT="/var/cache/pokehunter/staging/' in remote
    assert 'RELEASE_SUDOERS="/etc/sudoers.d/pokehunter-release"' in remote
    assert 'rm -f "${RELEASE_SUDOERS}"' in remote


def test_release_authorization_is_exact_and_separately_gated():
    authorizer = (ROOT / "deploy" / "authorize-release-root.sh").read_text()
    assert 'POKEHUNTER_RELEASE_APPROVED:-}" != "YES"' in authorizer
    assert "pokehunter-control *" not in authorizer
    assert "apply %s %s %s %s %s %s" in authorizer
