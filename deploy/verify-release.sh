#!/bin/bash
set -Eeuo pipefail
umask 077
export HOME=/root
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

EXPECTED_RELEASE="${1:?expected release identifier is required}"
readonly PUBLIC_URL="https://pokehunter-nq.duckdns.org/ebay/deletion"
if [[ "$#" -ne 1 || ! "${EXPECTED_RELEASE}" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "invalid expected release identifier"
  exit 2
fi
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
  if curl --disable --fail --silent --max-time 5 --output "${local_body}" \
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
  status="$(curl --disable --silent --show-error --output "${public_body}" \
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
