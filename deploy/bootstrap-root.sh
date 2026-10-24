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

for command in cut getent grep id install mktemp tr useradd visudo; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "missing bootstrap command: ${command}"
    exit 1
  fi
done

helpers=(
  remote-deploy.sh server-preflight.sh verify-release.sh probe-existing-ebay.py
  purge-legacy-deletion-db.py validate-release.py authorize-release-root.sh
)
units=(pokehunter-web.service pokehunter-monitor.service pokehunter-monitor.timer)
control_source="${SCRIPT_DIR}/pokehunter-control"
if [[ ! -f "${control_source}" || -L "${control_source}" ]]; then
  echo "missing or unsafe bootstrap source: ${control_source}"
  exit 1
fi
for helper in "${helpers[@]}"; do
  source="${SCRIPT_DIR}/${helper}"
  if [[ ! -f "${source}" || -L "${source}" ]]; then
    echo "missing or unsafe bootstrap source: ${source}"
    exit 1
  fi
done
for unit in "${units[@]}"; do
  source="${SCRIPT_DIR}/systemd/${unit}"
  destination="/etc/systemd/system/${unit}"
  if [[ ! -f "${source}" || -L "${source}" || -L "${destination}" ]]; then
    echo "missing or unsafe systemd unit path: ${unit}"
    exit 1
  fi
  if [[ -e "${destination}" ]] \
    && ! grep -Fq '/opt/pokehunter/current' "${destination}"; then
    echo "refusing to replace unrecognized unit ${unit}"
    exit 1
  fi
done
for path in "${LIBEXEC}" /opt/pokehunter /opt/pokehunter/releases \
  /var/cache/pokehunter /var/cache/pokehunter/staging /var/lib/pokehunter \
  /usr/local/sbin/pokehunter-control; do
  if [[ -L "${path}" ]]; then
    echo "refusing symbolic-link bootstrap target: ${path}"
    exit 1
  fi
done

temp_sudoers="$(mktemp)"
trap 'rm -f "${temp_sudoers}"' EXIT
{
  printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/pokehunter-control preflight\n' \
    "${DEPLOY_USER}"
  printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/pokehunter-control probe-ebay\n' \
    "${DEPLOY_USER}"
} >"${temp_sudoers}"
chmod 0440 "${temp_sudoers}"
visudo -cf "${temp_sudoers}" >/dev/null

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  useradd --create-home --home-dir "${DEPLOY_HOME}" --shell /bin/bash "${DEPLOY_USER}"
fi
if ! id pokehunter >/dev/null 2>&1; then
  useradd --system --user-group --home /var/lib/pokehunter \
    --shell /usr/sbin/nologin pokehunter
fi
if ! id pokehunter-web >/dev/null 2>&1; then
  useradd --system --user-group --home /nonexistent \
    --shell /usr/sbin/nologin pokehunter-web
fi

deploy_privileged_groups="$(
  id -nG "${DEPLOY_USER}" | tr ' ' '\n' | grep -Ex '(sudo|adm|docker|lxd)' || true
)"
if [[ -n "${deploy_privileged_groups}" ]]; then
  echo "refusing privileged group membership for ${DEPLOY_USER}: ${deploy_privileged_groups}"
  exit 1
fi
if [[ "$(getent passwd "${DEPLOY_USER}" | cut -d: -f6)" != "${DEPLOY_HOME}" ]]; then
  echo "unexpected ${DEPLOY_USER} home directory"
  exit 1
fi
for account in pokehunter pokehunter-web; do
  if [[ "$(id -gn "${account}")" != "${account}" ]] \
    || [[ "$(getent passwd "${account}" | cut -d: -f7)" != "/usr/sbin/nologin" ]]; then
    echo "refusing unexpected runtime account configuration: ${account}"
    exit 1
  fi
done

install -d -o root -g root -m 0755 "${LIBEXEC}" /opt/pokehunter /opt/pokehunter/releases
install -d -o root -g root -m 0700 /var/cache/pokehunter /var/cache/pokehunter/staging
install -d -o pokehunter -g pokehunter -m 0750 /var/lib/pokehunter
install -o root -g root -m 0755 "${SCRIPT_DIR}/pokehunter-control" /usr/local/sbin/pokehunter-control
for helper in "${helpers[@]}"; do
  install -o root -g root -m 0755 "${SCRIPT_DIR}/${helper}" "${LIBEXEC}/${helper}"
done
for unit in "${units[@]}"; do
  destination="/etc/systemd/system/${unit}"
  install -o root -g root -m 0644 "${SCRIPT_DIR}/systemd/${unit}" "${destination}"
done

install -o root -g root -m 0440 "${temp_sudoers}" "${SUDOERS}"
visudo -cf "${SUDOERS}" >/dev/null

echo "bootstrap=complete services_started=no caddy_touched=no hermes_touched=no"
