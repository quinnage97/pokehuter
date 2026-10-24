#!/usr/bin/env bash
set -Eeuo pipefail

# One-time, separately approved administrator bootstrap. This script does not deploy an app
# release, touch Caddy/Hermes, start services, migrate configuration, or delete data.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_USER="pokehunter-deploy"
DEPLOY_HOME="/var/lib/pokehunter-deploy"
LIBEXEC="/usr/local/libexec/pokehunter"
SUDOERS="/etc/sudoers.d/pokehunter-deploy"

if [[ "${EUID}" -ne 0 ]]; then
  echo "run this reviewed bootstrap as root"
  exit 1
fi
if [[ "${POKEHUNTER_BOOTSTRAP_APPROVED:-}" != "YES" ]]; then
  echo "bootstrap approval gate is closed"
  exit 1
fi

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  useradd --create-home --home-dir "${DEPLOY_HOME}" --shell /bin/bash "${DEPLOY_USER}"
fi
if ! id pokehunter >/dev/null 2>&1; then
  useradd --system --home /var/lib/pokehunter --shell /usr/sbin/nologin pokehunter
fi
if ! id pokehunter-web >/dev/null 2>&1; then
  useradd --system --home /nonexistent --shell /usr/sbin/nologin pokehunter-web
fi

install -d -o root -g root -m 0755 "${LIBEXEC}" /opt/pokehunter /opt/pokehunter/releases
install -d -o root -g root -m 0700 /var/cache/pokehunter /var/cache/pokehunter/staging
install -d -o pokehunter -g pokehunter -m 0750 /var/lib/pokehunter
install -o root -g root -m 0755 "${SCRIPT_DIR}/pokehunter-control" /usr/local/sbin/pokehunter-control
for helper in remote-deploy.sh server-preflight.sh verify-release.sh probe-existing-ebay.py \
  purge-legacy-deletion-db.py validate-release.py; do
  install -o root -g root -m 0755 "${SCRIPT_DIR}/${helper}" "${LIBEXEC}/${helper}"
done
for unit in pokehunter-web.service pokehunter-monitor.service pokehunter-monitor.timer; do
  destination="/etc/systemd/system/${unit}"
  if [[ -e "${destination}" ]] \
    && ! grep -Fq '/opt/pokehunter/current' "${destination}"; then
    echo "refusing to replace unrecognized unit ${unit}"
    exit 1
  fi
  install -o root -g root -m 0644 "${SCRIPT_DIR}/systemd/${unit}" "${destination}"
done

temp_sudoers="$(mktemp)"
trap 'rm -f "${temp_sudoers}"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/pokehunter-control *\n' \
  "${DEPLOY_USER}" >"${temp_sudoers}"
chmod 0440 "${temp_sudoers}"
visudo -cf "${temp_sudoers}" >/dev/null
install -o root -g root -m 0440 "${temp_sudoers}" "${SUDOERS}"
visudo -cf "${SUDOERS}" >/dev/null
systemctl daemon-reload

echo "bootstrap=complete services_started=no caddy_touched=no hermes_touched=no"
