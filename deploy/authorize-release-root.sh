#!/bin/bash
set -Eeuo pipefail
umask 077
export HOME=/root
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

# Installs a one-release sudo authorization only after a separate deployment approval. This helper
# is root-owned after bootstrap and is not callable through the deployment account's sudo policy.
if [[ "$#" -ne 6 ]]; then
  echo "usage: authorize-release-root.sh <artifact> <release> <sha256> <legacy-unit> <monitor> <purge>"
  exit 2
fi

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
  && [[ ! "${LEGACY_UNIT}" =~ ^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$ ]]; then
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
if [[ ! -f "${ARTIFACT}" || -L "${ARTIFACT}" ]] \
  || [[ "$(stat -c '%U' "${ARTIFACT}")" != "${DEPLOY_USER}" ]] \
  || (( 8#$(stat -c '%a' "${ARTIFACT}") & 022 )); then
  echo "release artifact must be a non-writable regular file owned by ${DEPLOY_USER}"
  exit 1
fi
if [[ "$(sha256sum -- "${ARTIFACT}" | awk '{print $1}')" != "${EXPECTED_SHA256}" ]]; then
  echo "release artifact checksum does not match the approved checksum"
  exit 1
fi
if [[ ! -f "${CONTROL}" || -L "${CONTROL}" ]] \
  || [[ "$(stat -c '%U' "${CONTROL}")" != "root" ]] \
  || (( 8#$(stat -c '%a' "${CONTROL}") & 022 )); then
  echo "PokéHunter control command is not a root-controlled regular file"
  exit 1
fi
for directory in /usr/local /usr/local/sbin /etc/sudoers.d; do
  if [[ ! -d "${directory}" || -L "${directory}" ]] \
    || [[ "$(stat -c '%U' "${directory}")" != "root" ]] \
    || (( 8#$(stat -c '%a' "${directory}") & 022 )); then
    echo "authorization directory is not root-controlled: ${directory}"
    exit 1
  fi
done
if [[ -e "${SUDOERS}" || -L "${SUDOERS}" ]]; then
  echo "refusing to overwrite an existing release authorization"
  exit 1
fi

temporary="$(mktemp /etc/sudoers.d/.pokehunter-release.XXXXXX)"
trap 'rm -f "${temporary}"' EXIT
printf '%s ALL=(root) NOPASSWD:NOSETENV: %s apply %s %s %s %s %s %s\n' \
  "${DEPLOY_USER}" "${CONTROL}" "${ARTIFACT}" "${RELEASE_ID}" \
  "${EXPECTED_SHA256}" "${LEGACY_UNIT}" "${ENABLE_MONITOR}" \
  "${PURGE_LEGACY_DATA}" >"${temporary}"
chown root:root "${temporary}"
chmod 0440 "${temporary}"
visudo -cf "${temporary}" >/dev/null
if ! ln "${temporary}" "${SUDOERS}"; then
  echo "release authorization appeared concurrently; refusing to overwrite it"
  exit 1
fi
visudo -cf "${SUDOERS}" >/dev/null

echo "release_authorization=installed release=${RELEASE_ID} one_time=yes"
