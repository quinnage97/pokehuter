#!/bin/bash
set -Eeuo pipefail
umask 077
export HOME=/root
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

# One-time, separately approved administrator bootstrap. This script does not deploy an app
# release, touch Caddy/Hermes, start services, migrate configuration, or delete data.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
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

for command in awk basename chmod chown cut dirname getent grep id install ln mktemp \
  sha256sum stat tr useradd visudo; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "missing bootstrap command: ${command}"
    exit 1
  fi
done

helpers=(
  remote-deploy.sh server-preflight.sh verify-release.sh probe-existing-ebay.py
  purge-legacy-deletion-db.py validate-release.py authorize-release-root.sh
  run-release-check.py migrate-config.py prepare-legacy-migration.py
)
units=(pokehunter-web.service pokehunter-monitor.service pokehunter-monitor.timer)

root_owned_not_writable() {
  local path="$1"
  [[ "$(stat -c '%u' "${path}")" == "0" ]] \
    && (( (8#$(stat -c '%a' "${path}") & 022) == 0 ))
}

require_root_directory() {
  local path="$1"
  if [[ ! -d "${path}" || -L "${path}" ]] || ! root_owned_not_writable "${path}"; then
    echo "bootstrap directory is not root-controlled: ${path}"
    exit 1
  fi
}

require_root_regular() {
  local path="$1"
  if [[ ! -f "${path}" || -L "${path}" ]] || ! root_owned_not_writable "${path}"; then
    echo "bootstrap source is not a root-controlled regular file: ${path}"
    exit 1
  fi
}

require_regular_target_or_absent() {
  local path="$1"
  if [[ -e "${path}" || -L "${path}" ]]; then
    if [[ ! -f "${path}" || -L "${path}" ]] || ! root_owned_not_writable "${path}"; then
      echo "refusing non-regular or non-root-controlled bootstrap target: ${path}"
      exit 1
    fi
  fi
}

require_directory_target_or_absent() {
  local path="$1"
  if [[ -e "${path}" || -L "${path}" ]]; then
    if [[ ! -d "${path}" || -L "${path}" ]] || ! root_owned_not_writable "${path}"; then
      echo "refusing non-directory or non-root-controlled bootstrap target: ${path}"
      exit 1
    fi
  fi
}

# The reviewed source must first be placed below a root-owned, non-writable path. This prevents a
# deployment user from replacing a helper between validation and installation.
source_ancestor="${SCRIPT_DIR}"
while [[ "${source_ancestor}" != "/" ]]; do
  require_root_directory "${source_ancestor}"
  source_ancestor="$(dirname -- "${source_ancestor}")"
done
require_root_directory "${SCRIPT_DIR}/systemd"
require_root_regular "${SCRIPT_DIR}/$(basename -- "${BASH_SOURCE[0]}")"
for trusted_parent in /usr/local /usr/local/sbin /etc/systemd/system /etc/sudoers.d \
  /opt /var/backups /var/cache /var/lib; do
  require_root_directory "${trusted_parent}"
done
if [[ -e /usr/local/libexec || -L /usr/local/libexec ]]; then
  require_root_directory /usr/local/libexec
fi
if [[ -e "${LIBEXEC}" || -L "${LIBEXEC}" ]]; then
  require_root_directory "${LIBEXEC}"
fi
control_source="${SCRIPT_DIR}/pokehunter-control"
require_root_regular "${control_source}"
for helper in "${helpers[@]}"; do
  source="${SCRIPT_DIR}/${helper}"
  require_root_regular "${source}"
  require_regular_target_or_absent "${LIBEXEC}/${helper}"
done
for unit in "${units[@]}"; do
  source="${SCRIPT_DIR}/systemd/${unit}"
  destination="/etc/systemd/system/${unit}"
  require_root_regular "${source}"
  require_regular_target_or_absent "${destination}"
  if [[ -e "${destination}" ]]; then
    if [[ "${unit}" == "pokehunter-monitor.timer" ]]; then
      identity='Unit=pokehunter-monitor.service'
    else
      identity='/opt/pokehunter/current/.venv/bin/pokehunter'
    fi
    if ! grep -Fq "${identity}" "${destination}"; then
      echo "refusing to replace unrecognized unit ${unit}"
      exit 1
    fi
  fi
done
for path in "${LIBEXEC}" /opt/pokehunter /opt/pokehunter/releases \
  /var/cache/pokehunter /var/cache/pokehunter/staging /var/lib/pokehunter \
  /var/backups/pokehunter; do
  require_directory_target_or_absent "${path}"
done
require_regular_target_or_absent /usr/local/sbin/pokehunter-control
require_regular_target_or_absent "${SUDOERS}"
if [[ -e /etc/sudoers.d/pokehunter-release || -L /etc/sudoers.d/pokehunter-release ]]; then
  echo "refusing bootstrap while a release authorization already exists"
  exit 1
fi

temp_sudoers="$(mktemp)"
trap 'rm -f "${temp_sudoers}"' EXIT
{
  printf '%s ALL=(root) NOPASSWD:NOSETENV: /usr/local/sbin/pokehunter-control preflight\n' \
    "${DEPLOY_USER}"
  printf '%s ALL=(root) NOPASSWD:NOSETENV: /usr/local/sbin/pokehunter-control probe-ebay\n' \
    "${DEPLOY_USER}"
} >"${temp_sudoers}"
chmod 0440 "${temp_sudoers}"
visudo -cf "${temp_sudoers}" >/dev/null

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  useradd --create-home --home-dir "${DEPLOY_HOME}" --shell /bin/bash "${DEPLOY_USER}"
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
if ! id pokehunter >/dev/null 2>&1; then
  useradd --system --user-group --home /var/lib/pokehunter \
    --shell /usr/sbin/nologin pokehunter
fi
if ! id pokehunter-web >/dev/null 2>&1; then
  useradd --system --user-group --home /nonexistent \
    --shell /usr/sbin/nologin pokehunter-web
fi
if ! id pokehunter-build >/dev/null 2>&1; then
  useradd --system --user-group --home /nonexistent \
    --shell /usr/sbin/nologin pokehunter-build
fi

for account in pokehunter pokehunter-web pokehunter-build; do
  if [[ "$(id -gn "${account}")" != "${account}" ]] \
    || [[ "$(getent passwd "${account}" | cut -d: -f7)" != "/usr/sbin/nologin" ]]; then
    echo "refusing unexpected runtime account configuration: ${account}"
    exit 1
  fi
done

install -d -o root -g root -m 0755 "${LIBEXEC}" /opt/pokehunter /opt/pokehunter/releases
install -d -o root -g root -m 0700 /var/cache/pokehunter /var/cache/pokehunter/staging
install -d -o root -g root -m 0700 /var/backups/pokehunter
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
