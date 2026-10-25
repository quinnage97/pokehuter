#!/bin/bash
set -Eeuo pipefail
umask 077
export HOME=/root
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

if [[ "$#" -ne 6 ]]; then
  echo "remote deployment requires exactly six arguments"
  exit 2
fi

ARTIFACT="${1:?release artifact path is required}"
RELEASE_ID="${2:?release identifier is required}"
EXPECTED_SHA256="${3:?release SHA-256 is required}"
LEGACY_UNIT="${4:-none}"
ENABLE_MONITOR="${5:-UNCHANGED}"
PURGE_LEGACY_DATA="${6:-NO}"
LEGACY_DB="/home/commandadmin/pokehunter/data/ebay_deletions.sqlite3"
LIBEXEC="/usr/local/libexec/pokehunter"
DEPLOY_USER="pokehunter-deploy"
BUILD_USER="pokehunter-build"
ROOT_ARTIFACT="/var/cache/pokehunter/staging/${RELEASE_ID}.tar.gz"
RELEASE_SUDOERS="/etc/sudoers.d/pokehunter-release"

if [[ "${EUID}" -ne 0 ]]; then
  echo "remote deployment must run as root"
  exit 1
fi
if [[ ! "${RELEASE_ID}" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "invalid release identifier"
  exit 1
fi
if [[ "${ARTIFACT}" != "/tmp/pokehunter-${RELEASE_ID}.tar.gz" ]]; then
  echo "unexpected release artifact path"
  exit 1
fi
if [[ ! -f "${ARTIFACT}" || -L "${ARTIFACT}" \
  || "$(stat -c '%U' "${ARTIFACT}")" != "${DEPLOY_USER}" ]] \
  || (( 8#$(stat -c '%a' "${ARTIFACT}") & 022 )); then
  echo "release artifact must be a non-writable regular file owned by ${DEPLOY_USER}"
  exit 1
fi
if [[ ! "${ENABLE_MONITOR}" =~ ^(YES|NO|UNCHANGED)$ ]]; then
  echo "POKEHUNTER_ENABLE_MONITOR must be YES, NO, or UNCHANGED"
  exit 1
fi
if [[ ! "${PURGE_LEGACY_DATA}" =~ ^(YES|NO)$ ]]; then
  echo "POKEHUNTER_PURGE_LEGACY_DELETION_DB must be YES or NO"
  exit 1
fi
if [[ "${LEGACY_UNIT}" != "none" ]] \
  && [[ ! "${LEGACY_UNIT}" =~ ^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$ ]]; then
  echo "invalid legacy unit name"
  exit 1
fi
if [[ "${LEGACY_UNIT,,}" =~ (hermes|caddy|docker|ssh|command.centre|ai.command) ]]; then
  echo "refusing protected legacy unit name"
  exit 1
fi

trusted_root_file() {
  local path="$1"
  if [[ ! -f "${path}" || -L "${path}" ]] \
    || [[ "$(stat -c '%U' "${path}")" != "root" ]] \
    || (( 8#$(stat -c '%a' "${path}") & 022 )); then
    echo "privileged file is not a root-controlled regular file: ${path}"
    exit 1
  fi
}

for helper in server-preflight.sh verify-release.sh validate-release.py \
  purge-legacy-deletion-db.py run-release-check.py; do
  trusted_root_file "${LIBEXEC}/${helper}"
done
if [[ ! -d "${LIBEXEC}" || -L "${LIBEXEC}" ]] \
  || [[ "$(stat -c '%U' "${LIBEXEC}")" != "root" ]] \
  || (( 8#$(stat -c '%a' "${LIBEXEC}") & 022 )); then
  echo "privileged helper directory is not root-controlled"
  exit 1
fi
for directory in /var/cache/pokehunter/staging /opt/pokehunter/releases; do
  if [[ ! -d "${directory}" || -L "${directory}" ]] \
    || [[ "$(stat -c '%U' "${directory}")" != "root" ]] \
    || (( 8#$(stat -c '%a' "${directory}") & 022 )); then
    echo "deployment directory is not root-controlled: ${directory}"
    exit 1
  fi
done
if [[ -e "${ROOT_ARTIFACT}" || -L "${ROOT_ARTIFACT}" ]]; then
  echo "refusing to overwrite an existing root staging artifact"
  exit 1
fi

# Copy out of the deployment user's control before validation to prevent checksum/extraction races.
install -o root -g root -m 0600 "${ARTIFACT}" "${ROOT_ARTIFACT}"
trap 'rm -f "${ROOT_ARTIFACT}"' EXIT
/usr/bin/python3 -I "${LIBEXEC}/validate-release.py" \
  "${ROOT_ARTIFACT}" "${RELEASE_ID}" "${EXPECTED_SHA256}"
rm -f "${ARTIFACT}"
ARTIFACT="${ROOT_ARTIFACT}"
expected_authorization="${DEPLOY_USER} ALL=(root) NOPASSWD:NOSETENV: /usr/local/sbin/pokehunter-control apply /tmp/pokehunter-${RELEASE_ID}.tar.gz ${RELEASE_ID} ${EXPECTED_SHA256} ${LEGACY_UNIT} ${ENABLE_MONITOR} ${PURGE_LEGACY_DATA}"
if [[ ! -f "${RELEASE_SUDOERS}" || -L "${RELEASE_SUDOERS}" ]] \
  || [[ "$(stat -c '%U:%G %a' "${RELEASE_SUDOERS}")" != "root:root 440" ]] \
  || ! grep -Fqx "${expected_authorization}" "${RELEASE_SUDOERS}"; then
  echo "missing or invalid one-release authorization"
  exit 1
fi
# Consume the exact authorization before any cutover work. A failed release requires a new,
# explicit administrator authorization and cannot be replayed by the deployment account.
rm -f "${RELEASE_SUDOERS}"
"${LIBEXEC}/server-preflight.sh"

public_challenge_hash() {
  local challenge="$1"
  curl --disable --fail --silent --show-error --max-time 10 \
    "https://pokehunter-nq.duckdns.org/ebay/deletion?challenge_code=${challenge}" \
    | /usr/bin/python3 -I -c \
      'import json,sys; value=json.load(sys.stdin).get("challengeResponse", ""); assert len(value) == 64; int(value, 16); print(value)'
}
continuity_challenge="release-${RELEASE_ID}"
legacy_challenge_hash="$(public_challenge_hash "${continuity_challenge}")"

required_keys=(
  EBAY_CLIENT_ID EBAY_CLIENT_SECRET EBAY_DELETION_ENDPOINT EBAY_VERIFICATION_TOKEN
  EBAY_DELIVERY_POSTCODE EBAY_AFFILIATE_CAMPAIGN_ID TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
  DATABASE_PATH
)
if [[ ! -f /etc/pokehunter.env || -L /etc/pokehunter.env ]]; then
  echo "configuration must be a regular file, not a symlink"
  exit 1
fi
config_owner="$(stat -c '%U:%G' /etc/pokehunter.env)"
config_mode="$(stat -c '%a' /etc/pokehunter.env)"
if [[ "${config_owner}" != "root:root" || "${config_mode}" != "600" ]]; then
  echo "invalid config ownership or mode; expected root:root 600"
  exit 1
fi
for key in "${required_keys[@]}"; do
  if ! grep -Eq "^${key}=.+" /etc/pokehunter.env; then
    echo "missing_config_key=${key}"
    exit 1
  fi
done
if ! grep -Fqx 'EBAY_DELETION_ENDPOINT=https://pokehunter-nq.duckdns.org/ebay/deletion' \
  /etc/pokehunter.env; then
  echo "existing public webhook URL must be preserved"
  exit 1
fi
if [[ -e "${LEGACY_DB}" && "${PURGE_LEGACY_DATA}" != "YES" ]]; then
  echo "legacy deletion database contains eBay user data; explicit purge approval is required"
  exit 1
fi

for unit in pokehunter-web.service pokehunter-monitor.service; do
  unit_path="/etc/systemd/system/${unit}"
  if [[ ! -f "${unit_path}" || -L "${unit_path}" ]] \
    || [[ "$(stat -c '%U' "${unit_path}")" != "root" ]] \
    || (( 8#$(stat -c '%a' "${unit_path}") & 022 )); then
    echo "refusing unsafe unit file ${unit}"
    exit 1
  fi
  if ! grep -Fq '/opt/pokehunter/current/.venv/bin/pokehunter' "${unit_path}"; then
    echo "refusing to replace unrecognized unit ${unit}"
    exit 1
  fi
done
timer_path="/etc/systemd/system/pokehunter-monitor.timer"
if [[ ! -f "${timer_path}" || -L "${timer_path}" ]] \
  || [[ "$(stat -c '%U' "${timer_path}")" != "root" ]] \
  || (( 8#$(stat -c '%a' "${timer_path}") & 022 )); then
  echo "refusing unsafe unit file pokehunter-monitor.timer"
  exit 1
fi
if ! grep -Fq 'Unit=pokehunter-monitor.service' "${timer_path}"; then
  echo "refusing to replace unrecognized unit pokehunter-monitor.timer"
  exit 1
fi

hermes_fingerprint() {
  systemctl list-units --type=service --all --no-legend \
    | awk 'tolower($1) ~ /(hermes|ai-command|command-centre)/ {print $1, $3, $4}'
}

before_fingerprint="$(mktemp)"
after_fingerprint="$(mktemp)"
previous_target="$(readlink -f /opt/pokehunter/current 2>/dev/null || true)"
previous_web_enabled=0
previous_web_active=0
previous_monitor_enabled=0
previous_monitor_active=0
systemctl is-enabled --quiet pokehunter-web.service 2>/dev/null && previous_web_enabled=1
systemctl is-active --quiet pokehunter-web.service 2>/dev/null && previous_web_active=1
systemctl is-enabled --quiet pokehunter-monitor.timer 2>/dev/null && previous_monitor_enabled=1
systemctl is-active --quiet pokehunter-monitor.timer 2>/dev/null && previous_monitor_active=1
legacy_stopped=0
legacy_was_enabled=0
activated=0

cleanup() {
  pkill -KILL -u "${BUILD_USER}" >/dev/null 2>&1 || true
  rm -f "${before_fingerprint}" "${after_fingerprint}" "${ARTIFACT}"
}
rollback() {
  status=$?
  if [[ "${status}" -ne 0 ]]; then
    if [[ "${activated}" -eq 1 ]]; then
      systemctl disable --now pokehunter-monitor.timer >/dev/null 2>&1 || true
      systemctl disable --now pokehunter-web.service >/dev/null 2>&1 || true
      if [[ -n "${previous_target}" && -d "${previous_target}" ]]; then
        ln -sfn "${previous_target}" /opt/pokehunter/current.rollback
        mv -Tf /opt/pokehunter/current.rollback /opt/pokehunter/current
      else
        rm -f /opt/pokehunter/current || true
      fi
      systemctl daemon-reload >/dev/null 2>&1 || true
      if [[ "${previous_web_active}" -eq 1 ]]; then
        systemctl start pokehunter-web.service >/dev/null 2>&1 || true
      fi
      if [[ "${previous_web_enabled}" -eq 1 ]]; then
        systemctl enable pokehunter-web.service >/dev/null 2>&1 || true
      fi
      if [[ "${previous_monitor_enabled}" -eq 1 ]]; then
        systemctl enable pokehunter-monitor.timer >/dev/null 2>&1 || true
      fi
      if [[ "${previous_monitor_active}" -eq 1 ]]; then
        systemctl start pokehunter-monitor.timer >/dev/null 2>&1 || true
      fi
    fi
    if [[ "${legacy_stopped}" -eq 1 ]]; then
      if [[ "${legacy_was_enabled}" -eq 1 ]]; then
        systemctl enable "${LEGACY_UNIT}" >/dev/null 2>&1 || true
      fi
      systemctl start "${LEGACY_UNIT}" >/dev/null 2>&1 || true
    fi
  fi
  cleanup
  exit "${status}"
}
trap rollback EXIT
hermes_fingerprint >"${before_fingerprint}"

for account in "${DEPLOY_USER}" "${BUILD_USER}" pokehunter pokehunter-web; do
  if ! id "${account}" >/dev/null 2>&1; then
    echo "missing bootstrapped account: ${account}"
    exit 1
  fi
done
for unit in pokehunter-web.service pokehunter-monitor.service pokehunter-monitor.timer; do
  if [[ ! -f "/etc/systemd/system/${unit}" ]]; then
    echo "missing bootstrapped unit: ${unit}"
    exit 1
  fi
done

release_dir="/opt/pokehunter/releases/${RELEASE_ID}"
if [[ -e "${release_dir}" || -L "${release_dir}" ]]; then
  echo "release already exists: ${release_dir}"
  exit 1
fi
install -d -o "${BUILD_USER}" -g "${BUILD_USER}" -m 0755 "${release_dir}"
# Archive parsing and all package build hooks run without root privileges. The deployment account
# can upload only the approved archive; a separate non-login build account owns temporary output.
runuser -u "${BUILD_USER}" -- env -i HOME="${release_dir}" \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  tar --extract --gzip --no-same-owner --no-same-permissions \
  --file - --directory "${release_dir}" <"${ARTIFACT}"

nice -n 10 ionice -c2 -n7 runuser -u "${BUILD_USER}" -- \
  env -i HOME="${release_dir}" PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 python3 -m venv "${release_dir}/.venv"
nice -n 10 ionice -c2 -n7 runuser -u "${BUILD_USER}" -- \
  env -i HOME="${release_dir}" PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 PIP_CONFIG_FILE=/dev/null PIP_NO_INPUT=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CONSTRAINT="${release_dir}/deploy/constraints.txt" \
  "${release_dir}/.venv/bin/pip" install \
  --disable-pip-version-check --require-virtualenv \
  --constraint "${release_dir}/deploy/constraints.txt" "${release_dir}"
nice -n 10 ionice -c2 -n7 runuser -u "${BUILD_USER}" -- \
  env -i HOME="${release_dir}" PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 "${release_dir}/.venv/bin/pip" check
nice -n 10 ionice -c2 -n7 runuser -u "${BUILD_USER}" -- \
  env -i HOME="${release_dir}" PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 "${release_dir}/.venv/bin/python" -m compileall -q \
  "${release_dir}/src"
pkill -KILL -u "${BUILD_USER}" >/dev/null 2>&1 || true
if pgrep -u "${BUILD_USER}" >/dev/null 2>&1; then
  echo "build account still has running processes"
  exit 1
fi
chown -hR root:root "${release_dir}"

# Probe OAuth and Browse permissions before changing the live service. Values remain inside the
# root-owned EnvironmentFile and are never printed, sourced as shell code, or put on the command
# line. The fixed launcher parses an allowlist, drops all groups and privileges, then executes.
/usr/bin/python3 -I "${LIBEXEC}/run-release-check.py" "${RELEASE_ID}" check-ebay
/usr/bin/python3 -I "${LIBEXEC}/run-release-check.py" "${RELEASE_ID}" check-telegram

ln -sfn "${release_dir}" /opt/pokehunter/current.next
mv -Tf /opt/pokehunter/current.next /opt/pokehunter/current
activated=1
systemd-analyze verify /etc/systemd/system/pokehunter-web.service \
  /etc/systemd/system/pokehunter-monitor.service \
  /etc/systemd/system/pokehunter-monitor.timer

if [[ "${LEGACY_UNIT}" != "none" ]]; then
  legacy_exec="$(systemctl show "${LEGACY_UNIT}" --property=ExecStart --value)"
  if [[ "${legacy_exec}" != *ebay_notifications.py* ]] \
    && [[ "${legacy_exec}" != *pokehunter* ]]; then
    echo "legacy unit identity check failed"
    exit 1
  fi
  legacy_pid="$(systemctl show "${LEGACY_UNIT}" --property=MainPID --value)"
  if [[ ! "${legacy_pid}" =~ ^[1-9][0-9]*$ ]] \
    || ! ss -ltnpH "sport = :8765" \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | grep -Fxq "${legacy_pid}"; then
    echo "legacy unit does not own the port 8765 listener"
    exit 1
  fi
  systemctl is-enabled --quiet "${LEGACY_UNIT}" 2>/dev/null && legacy_was_enabled=1
  systemctl stop "${LEGACY_UNIT}"
  legacy_stopped=1
else
  if ss -ltnH "sport = :8765" | grep -q .; then
    echo "port 8765 is occupied but no validated legacy unit was supplied"
    exit 1
  fi
fi

systemctl daemon-reload
systemctl enable --now pokehunter-web.service

if [[ "${ENABLE_MONITOR}" == "YES" ]]; then
  systemctl enable --now pokehunter-monitor.timer
elif [[ "${ENABLE_MONITOR}" == "NO" ]]; then
  systemctl disable --now pokehunter-monitor.timer >/dev/null 2>&1 || true
else
  echo "monitor_timer=unchanged"
fi

"${LIBEXEC}/verify-release.sh" "${RELEASE_ID}"
replacement_challenge_hash="$(public_challenge_hash "${continuity_challenge}")"
if [[ "${replacement_challenge_hash}" != "${legacy_challenge_hash}" ]]; then
  echo "webhook challenge response changed; rolling back PokéHunter"
  exit 1
fi
hermes_fingerprint >"${after_fingerprint}"
if ! cmp -s "${before_fingerprint}" "${after_fingerprint}"; then
  echo "Hermes fingerprint changed; rolling back PokéHunter"
  exit 1
fi

if [[ "${legacy_stopped}" -eq 1 ]]; then
  systemctl disable "${LEGACY_UNIT}"
fi
if [[ "${PURGE_LEGACY_DATA}" == "YES" ]]; then
  # This fixed root-owned helper refuses unexpected tables or columns. It never touches backups.
  /usr/bin/python3 -I "${LIBEXEC}/purge-legacy-deletion-db.py"
fi

activated=0
trap cleanup EXIT
echo "deployment=successful release=${RELEASE_ID} monitor_enabled=${ENABLE_MONITOR}"
