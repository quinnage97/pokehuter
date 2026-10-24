#!/usr/bin/env bash
set -euo pipefail

EXPECTED_RELEASE="${1:?expected release identifier is required}"
PUBLIC_URL="${POKEHUNTER_PUBLIC_URL:-https://pokehunter-nq.duckdns.org/ebay/deletion}"
CURRENT="$(readlink -f /opt/pokehunter/current)"

if [[ "${CURRENT}" != "/opt/pokehunter/releases/${EXPECTED_RELEASE}" ]]; then
  echo "unexpected_current_release=${CURRENT}"
  exit 1
fi

systemctl is-active --quiet pokehunter-web.service
local_body="$(mktemp)"
public_body="$(mktemp)"
trap 'rm -f "${local_body}" "${public_body}"' EXIT

local_healthy=0
for _ in {1..10}; do
  if curl --fail --silent --max-time 5 --output "${local_body}" \
    http://127.0.0.1:8765/healthz \
    && grep -Fq '"status":"ok"' "${local_body}"; then
    local_healthy=1
    break
  fi
  sleep 1
done
[[ "${local_healthy}" -eq 1 ]]

public_healthy=0
for _ in {1..10}; do
  status="$(curl --silent --show-error --output "${public_body}" \
    --write-out '%{http_code}' --max-time 10 \
    "${PUBLIC_URL}?challenge_code=release-verification" || true)"
  if [[ "${status}" == "200" ]] \
    && grep -Eq '^\{"challengeResponse"[[:space:]]*:[[:space:]]*"[0-9a-f]{64}"\}$' \
      "${public_body}"; then
    public_healthy=1
    break
  fi
  sleep 1
done
[[ "${public_healthy}" -eq 1 ]]

echo "release_verification=passed"
