#!/usr/bin/env bash
# Point the public web address at the PokéHunter website, keeping /ebay/* on the existing
# eBay deletion webhook. Backs up the Caddyfile and restores it if anything fails. Run as root:
#   bash /opt/pokehunter-app/deploy/setup-caddy.sh
set -euo pipefail

DOMAIN="${POKEHUNTER_DOMAIN:-pokehunter-nq.duckdns.org}"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
SITE_PORT=8766

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this as root." >&2
  exit 1
fi
if [[ ! -f "${CADDYFILE}" ]] || ! command -v caddy >/dev/null; then
  echo "Could not find Caddy at ${CADDYFILE}. Send a screenshot of this message." >&2
  exit 1
fi

if grep -E "^[^#]*,.*${DOMAIN//./\\.}|^[^#]*${DOMAIN//./\\.}[^{]*," "${CADDYFILE}" >/dev/null; then
  echo "${DOMAIN} shares a block with other sites; not changing it automatically." >&2
  echo "Send a screenshot of: cat ${CADDYFILE}" >&2
  exit 1
fi

backup="${CADDYFILE}.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "${CADDYFILE}" "${backup}"
echo "Backup saved: ${backup}"

# The upstream the current site block uses (the eBay webhook), defaulting to port 8765.
webhook="$(awk -v d="${DOMAIN}" '
  !inside && index($0, d) && /\{[[:space:]]*$/ { inside = 1; depth = 0 }
  inside {
    if (!found && $1 == "reverse_proxy" && $2 != "") { print $2; found = 1 }
    depth += gsub(/\{/, "{"); depth -= gsub(/\}/, "}")
    if (depth == 0) inside = 0
  }' "${CADDYFILE}" | head -n 1)"
if [[ -z "${webhook}" || "${webhook}" == *":${SITE_PORT}" ]]; then
  webhook="127.0.0.1:8765"
fi
echo "eBay webhook stays on: ${webhook}"

block="${DOMAIN} {
    # eBay account-deletion webhook (existing service)
    handle /ebay/* {
        reverse_proxy ${webhook}
    }
    # PokéHunter website
    handle {
        reverse_proxy 127.0.0.1:${SITE_PORT}
    }
}"

tmp="$(mktemp)"
BLOCK="${block}" awk -v d="${DOMAIN}" '
  !inside && !done && index($0, d) && /\{[[:space:]]*$/ {
    inside = 1; depth = 0
  }
  inside {
    depth += gsub(/\{/, "{"); depth -= gsub(/\}/, "}")
    if (depth == 0) { inside = 0; done = 1; print ENVIRON["BLOCK"] }
    next
  }
  { print }
  END { if (!done) { print ""; print ENVIRON["BLOCK"] } }
' "${CADDYFILE}" >"${tmp}"

restore() {
  cp -p "${backup}" "${CADDYFILE}"
  echo "Something went wrong, so the original Caddy settings were put back." >&2
  echo "Send a screenshot of the messages above." >&2
  exit 1
}

cat "${tmp}" >"${CADDYFILE}"
rm -f "${tmp}"
caddy validate --config "${CADDYFILE}" --adapter caddyfile >/dev/null 2>&1 || {
  caddy validate --config "${CADDYFILE}" --adapter caddyfile 2>&1 | tail -n 5
  restore
}
if systemctl is-active --quiet caddy; then
  systemctl reload caddy || restore
else
  caddy reload --config "${CADDYFILE}" --adapter caddyfile || restore
fi

sleep 2
site="$(curl -s -o /dev/null -w '%{http_code}' "https://${DOMAIN}/healthz" || true)"
hook="$(curl -s "https://${DOMAIN}/ebay/deletion?challenge_code=setup-check" || true)"
echo
if [[ "${site}" == "200" ]]; then
  echo "Website OK: open https://${DOMAIN}/ and log in with your site password."
else
  echo "Website check returned HTTP ${site}."
fi
if [[ "${hook}" == *challengeResponse* ]]; then
  echo "eBay webhook OK."
else
  echo "eBay webhook did not answer as expected. Send a screenshot of this."
fi
