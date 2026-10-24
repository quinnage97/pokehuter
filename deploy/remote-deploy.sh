#!/usr/bin/env bash
set -Eeuo pipefail

ARTIFACT="${1:?release artifact path is required}"
RELEASE_ID="${2:?release identifier is required}"
EXPECTED_SHA256="${3:?release SHA-256 is required}"
LEGACY_UNIT="${4:-none}"
ENABLE_MONITOR="${POKEHUNTER_ENABLE_MONITOR:-UNCHANGED}"
PURGE_LEGACY_DATA="${POKEHUNTER_PURGE_LEGACY_DELETION_DB:-NO}"
LEGACY_DB="/opt/pokehunter/data/ebay_deletions.sqlite3"

if [[ "${EUID}" -ne 0 ]]; then
  echo "remote deployment must run as root"
  exit 1
fi
if [[ "${POKEHUNTER_DEPLOY_APPROVED:-}" != "YES" ]]; then
  echo "deployment approval gate is closed"
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
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "missing sha256sum"
  exit 1
fi
if [[ ! "${EXPECTED_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
  || [[ "$(sha256sum "${ARTIFACT}" | awk '{print $1}')" != "${EXPECTED_SHA256}" ]]; then
  echo "release artifact checksum mismatch"
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
  && [[ ! "${LEGACY_UNIT}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]]; then
  echo "invalid legacy unit name"
  exit 1
fi
if [[ "${LEGACY_UNIT,,}" =~ (hermes|caddy|docker|ssh|command.centre|ai.command) ]]; then
  echo "refusing protected legacy unit name"
  exit 1
fi

if tar --list --gzip --file "${ARTIFACT}" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "unsafe release archive"
  exit 1
fi
preflight_script="$(mktemp)"
tar --extract --gzip --to-stdout --file "${ARTIFACT}" deploy/server-preflight.sh \
  >"${preflight_script}"
bash "${preflight_script}"
rm -f "${preflight_script}"

public_challenge_hash() {
  local challenge="$1"
  curl --fail --silent --show-error --max-time 10 \
    "https://pokehunter-nq.duckdns.org/ebay/deletion?challenge_code=${challenge}" \
    | python3 -c \
      'import json,sys; value=json.load(sys.stdin).get("challengeResponse", ""); assert len(value) == 64; int(value, 16); print(value)'
}
continuity_challenge="release-${RELEASE_ID}"
legacy_challenge_hash="$(public_challenge_hash "${continuity_challenge}")"

required_keys=(
  EBAY_CLIENT_ID EBAY_CLIENT_SECRET EBAY_DELETION_ENDPOINT EBAY_VERIFICATION_TOKEN
  EBAY_DELIVERY_POSTCODE EBAY_AFFILIATE_CAMPAIGN_ID TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
  DATABASE_PATH
)
if [[ ! -r /etc/pokehunter.env ]]; then
  echo "missing /etc/pokehunter.env"
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
  if [[ -e "${unit_path}" ]] \
    && ! grep -Fq '/opt/pokehunter/current/.venv/bin/pokehunter' "${unit_path}"; then
    echo "refusing to replace unrecognized unit ${unit}"
    exit 1
  fi
done
timer_path="/etc/systemd/system/pokehunter-monitor.timer"
if [[ -e "${timer_path}" ]] \
  && ! grep -Fq 'Unit=pokehunter-monitor.service' "${timer_path}"; then
  echo "refusing to replace unrecognized unit pokehunter-monitor.timer"
  exit 1
fi

hermes_fingerprint() {
  systemctl list-units --type=service --all --no-legend \
    | awk 'tolower($1) ~ /(hermes|ai-command|command-centre)/ {print $1, $3, $4}'
  if command -v docker >/dev/null 2>&1; then
    docker ps --format '{{.ID}} {{.Names}}' \
      | awk 'tolower($0) ~ /(hermes|ai-command|command-centre)/'
  fi
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

if ! id pokehunter >/dev/null 2>&1; then
  useradd --system --home /var/lib/pokehunter --shell /usr/sbin/nologin pokehunter
fi
if ! id pokehunter-web >/dev/null 2>&1; then
  useradd --system --home /nonexistent --shell /usr/sbin/nologin pokehunter-web
fi
install -d -o root -g root -m 0755 /opt/pokehunter /opt/pokehunter/releases
install -d -o pokehunter -g pokehunter -m 0750 /var/lib/pokehunter

release_dir="/opt/pokehunter/releases/${RELEASE_ID}"
if [[ -e "${release_dir}" ]]; then
  echo "release already exists: ${release_dir}"
  exit 1
fi
install -d -o pokehunter -g pokehunter -m 0755 "${release_dir}"
tar --extract --gzip --no-same-owner --no-same-permissions \
  --file "${ARTIFACT}" --directory "${release_dir}"
chown -R pokehunter:pokehunter "${release_dir}"

nice -n 10 ionice -c2 -n7 runuser -u pokehunter -- \
  python3 -m venv "${release_dir}/.venv"
nice -n 10 ionice -c2 -n7 runuser -u pokehunter -- \
  env PIP_CONSTRAINT="${release_dir}/deploy/constraints.txt" \
  "${release_dir}/.venv/bin/pip" install \
  --disable-pip-version-check --require-virtualenv \
  --constraint "${release_dir}/deploy/constraints.txt" "${release_dir}"
nice -n 10 ionice -c2 -n7 runuser -u pokehunter -- \
  "${release_dir}/.venv/bin/pip" check
nice -n 10 ionice -c2 -n7 runuser -u pokehunter -- \
  "${release_dir}/.venv/bin/python" -m compileall -q \
  "${release_dir}/src"
chown -R root:root "${release_dir}"

# Probe OAuth and Browse permissions before changing the live service. Values remain inside the
# root-owned EnvironmentFile and are never printed or passed on the command line.
/bin/bash -c \
  'set -a; source /etc/pokehunter.env; set +a; exec runuser -u pokehunter --preserve-environment -- "$1" check-ebay' \
  bash "${release_dir}/.venv/bin/pokehunter"
/bin/bash -c \
  'set -a; source /etc/pokehunter.env; set +a; exec runuser -u pokehunter --preserve-environment -- "$1" check-telegram' \
  bash "${release_dir}/.venv/bin/pokehunter"

install -o root -g root -m 0644 \
  "${release_dir}/deploy/systemd/pokehunter-web.service" /etc/systemd/system/
install -o root -g root -m 0644 \
  "${release_dir}/deploy/systemd/pokehunter-monitor.service" /etc/systemd/system/
install -o root -g root -m 0644 \
  "${release_dir}/deploy/systemd/pokehunter-monitor.timer" /etc/systemd/system/

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

bash "${release_dir}/deploy/verify-release.sh" "${RELEASE_ID}"
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
  python3 "${release_dir}/deploy/purge-legacy-deletion-db.py"
fi

activated=0
trap cleanup EXIT
echo "deployment=successful release=${RELEASE_ID} monitor_enabled=${ENABLE_MONITOR}"
