#!/usr/bin/env bash
set -Eeuo pipefail

# Installs a one-release sudo authorization only after a separate deployment approval. This helper
# is root-owned after bootstrap and is not callable through the deployment account's sudo policy.
ARTIFACT="${1:?release artifact path is required}"
RELEASE_ID="${2:?release identifier is required}"
EXPECTED_SHA256="${3:?release SHA-256 is required}"
LEGACY_UNIT="${4:-none}"
ENABLE_MONITOR="${5:-UNCHANGED}"
PURGE_LEGACY_DATA="${6:-NO}"
DEPLOY_USER="pokehunter-deploy"
SUDOERS="/etc/sudoers.d/pokehunter-release"
CONTROL="/usr/local/sbin/pokehunter-control"

if [[ "${EUID}" -ne 0 ]]; then
  echo "run this reviewed authorization helper as root"
  exit 1
fi
if [[ "${POKEHUNTER_RELEASE_APPROVED:-}" != "YES" ]]; then
  echo "release approval gate is closed"
  exit 1
fi
if [[ ! "${RELEASE_ID}" =~ ^[0-9a-f]{7,40}$ ]] \
  || [[ "${ARTIFACT}" != "/tmp/pokehunter-${RELEASE_ID}.tar.gz" ]] \
  || [[ ! "${EXPECTED_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "invalid release identity"
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
if [[ ! "${ENABLE_MONITOR}" =~ ^(YES|NO|UNCHANGED)$ ]] \
  || [[ ! "${PURGE_LEGACY_DATA}" =~ ^(YES|NO)$ ]]; then
  echo "invalid release approval flags"
  exit 1
fi
if [[ -e "${SUDOERS}" ]]; then
  echo "refusing to overwrite an existing release authorization"
  exit 1
fi

temporary="$(mktemp)"
trap 'rm -f "${temporary}"' EXIT
printf '%s ALL=(root) NOPASSWD: %s apply %s %s %s %s %s %s\n' \
  "${DEPLOY_USER}" "${CONTROL}" "${ARTIFACT}" "${RELEASE_ID}" \
  "${EXPECTED_SHA256}" "${LEGACY_UNIT}" "${ENABLE_MONITOR}" \
  "${PURGE_LEGACY_DATA}" >"${temporary}"
chmod 0440 "${temporary}"
visudo -cf "${temporary}" >/dev/null
install -o root -g root -m 0440 "${temporary}" "${SUDOERS}"
visudo -cf "${SUDOERS}" >/dev/null

echo "release_authorization=installed release=${RELEASE_ID} one_time=yes"
