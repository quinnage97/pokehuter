#!/bin/bash
set -Eeuo pipefail
umask 077
export HOME=/root
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

readonly PORT="8765"
readonly PUBLIC_URL="https://pokehunter-nq.duckdns.org/ebay/deletion"

echo "PokéHunter server preflight (read-only)"
echo "host=$(hostname -f 2>/dev/null || hostname) port=${PORT}"

for command in python3 systemctl systemd-analyze ss curl env flock install ionice nice pgrep \
  pkill runuser sha256sum tar useradd; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "missing_command=${command}"
    exit 1
  fi
done

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "python_3_11_or_newer=required"
  exit 1
fi
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python_venv=unavailable"
  exit 1
fi
available_kib="$(df -Pk /opt | awk 'NR == 2 {print $4}')"
if [[ ! "${available_kib}" =~ ^[0-9]+$ || "${available_kib}" -lt 524288 ]]; then
  echo "disk_space=insufficient"
  exit 1
fi
echo "disk_available_kib=${available_kib}"

echo "os=$(awk -F= '$1 == "PRETTY_NAME" {gsub(/"/, "", $2); print $2}' /etc/os-release)"
echo "python=$(python3 --version 2>&1)"

echo "listener_${PORT}:"
ss -ltnpH "sport = :${PORT}" || true

echo "relevant_systemd_units:"
systemctl list-units --type=service --all --no-legend \
  | awk 'tolower($1) ~ /(pokehunter|ebay|hermes|ai-command|command-centre|caddy)/ {print $1, $3, $4}' \
  || true

echo "container_runtime=not-inspected-by-policy"

echo "caddy_state=$(systemctl is-active caddy.service 2>/dev/null || true)"
if [[ "${EUID}" -eq 0 ]] && command -v caddy >/dev/null 2>&1 \
  && [[ -r /etc/caddy/Caddyfile ]]; then
  caddy validate --config /etc/caddy/Caddyfile >/dev/null
  echo "caddy_config=valid"
else
  echo "caddy_config=not-validated-without-privilege"
fi

if [[ -e /opt/pokehunter ]]; then
  echo "app_path=present"
else
  echo "app_path=absent-or-not-visible"
fi

if [[ -e /opt/pokehunter/data/ebay_deletions.sqlite3 ]]; then
  echo "legacy_deletion_database=present bytes=$(stat -c '%s' /opt/pokehunter/data/ebay_deletions.sqlite3)"
else
  echo "legacy_deletion_database=absent-or-not-visible"
fi

if [[ -f /etc/pokehunter.env && ! -L /etc/pokehunter.env ]] \
  && [[ "$(stat -c '%U:%G' /etc/pokehunter.env)" == "root:root" ]]; then
  mode="$(stat -c '%a' /etc/pokehunter.env)"
  echo "config=present mode=${mode} keys:"
  sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' /etc/pokehunter.env | sort -u
else
  echo "config=absent-or-not-a-root-owned-regular-file"
fi

body_file="$(mktemp)"
trap 'rm -f "${body_file}"' EXIT
status="$(curl --disable --silent --show-error --output "${body_file}" \
  --write-out '%{http_code}' --max-time 10 "${PUBLIC_URL}?challenge_code=deployment-preflight" || true)"
if [[ "${status}" == "200" ]] \
  && grep -Eq '^\{"challengeResponse"[[:space:]]*:[[:space:]]*"[0-9a-f]{64}"\}$' \
    "${body_file}"; then
  echo "public_webhook=healthy"
else
  echo "public_webhook=unexpected_status_${status}"
  exit 1
fi

echo "preflight=passed"
