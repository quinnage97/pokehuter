#!/usr/bin/env bash
set -euo pipefail

PORT="${POKEHUNTER_PORT:-8765}"
PUBLIC_URL="${POKEHUNTER_PUBLIC_URL:-https://pokehunter-nq.duckdns.org/ebay/deletion}"

echo "PokéHunter server preflight (read-only)"
echo "host=$(hostname -f 2>/dev/null || hostname) port=${PORT}"

for command in python3 systemctl systemd-analyze ss curl flock sha256sum; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "missing_command=${command}"
    exit 1
  fi
done

echo "os=$(awk -F= '$1 == "PRETTY_NAME" {gsub(/\"/, "", $2); print $2}' /etc/os-release)"
echo "python=$(python3 --version 2>&1)"

echo "listener_${PORT}:"
ss -ltnpH "sport = :${PORT}" || true

echo "relevant_systemd_units:"
systemctl list-units --type=service --all --no-legend \
  | awk 'tolower($1) ~ /(pokehunter|ebay|hermes|ai-command|command-centre|caddy)/ {print $1, $3, $4}' \
  || true

echo "relevant_containers:"
if command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.ID}} {{.Names}} {{.Status}}' \
    | awk 'tolower($0) ~ /(hermes|ai-command|command-centre|pokehunter|ebay)/' \
    || true
else
  echo "docker=not-installed"
fi

echo "caddy_state=$(systemctl is-active caddy.service 2>/dev/null || true)"
if command -v caddy >/dev/null 2>&1 && [[ -r /etc/caddy/Caddyfile ]]; then
  caddy validate --config /etc/caddy/Caddyfile >/dev/null
  echo "caddy_config=valid"
else
  echo "caddy_config=not-validated"
fi

if [[ -e /opt/pokehunter ]]; then
  echo "app_path=present"
else
  echo "app_path=absent"
fi

if [[ -r /etc/pokehunter.env ]]; then
  mode="$(stat -c '%a' /etc/pokehunter.env)"
  echo "config=present mode=${mode} keys:"
  sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' /etc/pokehunter.env | sort -u
else
  echo "config=absent"
fi

status="$(curl --silent --show-error --output /tmp/pokehunter-preflight-body.$$ \
  --write-out '%{http_code}' --max-time 10 "${PUBLIC_URL}?challenge_code=deployment-preflight" || true)"
trap 'rm -f /tmp/pokehunter-preflight-body.$$' EXIT
if [[ "${status}" == "200" ]] \
  && grep -Eq '^\{"challengeResponse"[[:space:]]*:[[:space:]]*"[0-9a-f]{64}"\}$' \
    /tmp/pokehunter-preflight-body.$$; then
  echo "public_webhook=healthy"
else
  echo "public_webhook=unexpected_status_${status}"
  exit 1
fi

echo "preflight=passed"
