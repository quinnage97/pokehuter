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
curl --fail --silent --max-time 5 http://127.0.0.1:8765/healthz \
  | grep -Fq '"status":"ok"'

status="$(curl --silent --show-error --output /tmp/pokehunter-verify-body.$$ \
  --write-out '%{http_code}' --max-time 10 "${PUBLIC_URL}?challenge_code=release-verification" || true)"
trap 'rm -f /tmp/pokehunter-verify-body.$$' EXIT
[[ "${status}" == "200" ]]
grep -Eq '^\{"challengeResponse"[[:space:]]*:[[:space:]]*"[0-9a-f]{64}"\}$' \
  /tmp/pokehunter-verify-body.$$

echo "release_verification=passed"
