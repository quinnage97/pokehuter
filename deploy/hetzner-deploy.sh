#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
SSH_TARGET="${2:-}"
LEGACY_UNIT="${3:-none}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "${SCRIPT_DIR}/.." rev-parse --show-toplevel)"
ENABLE_MONITOR="${POKEHUNTER_ENABLE_MONITOR:-UNCHANGED}"
PURGE_LEGACY_DATA="${POKEHUNTER_PURGE_LEGACY_DELETION_DB:-NO}"

if [[ -z "${MODE}" || -z "${SSH_TARGET}" ]]; then
  echo "usage: $0 preflight|probe-ebay|apply <ssh-target> [legacy-unit.service]"
  exit 2
fi
if [[ ! "${SSH_TARGET}" =~ ^([A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$ ]]; then
  echo "invalid SSH target; configure ports and identity files in SSH config"
  exit 2
fi
if [[ "${LEGACY_UNIT}" != "none" ]] \
  && [[ ! "${LEGACY_UNIT}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]]; then
  echo "invalid legacy unit name"
  exit 2
fi
if [[ ! "${ENABLE_MONITOR}" =~ ^(YES|NO|UNCHANGED)$ ]]; then
  echo "POKEHUNTER_ENABLE_MONITOR must be YES, NO, or UNCHANGED"
  exit 2
fi
if [[ ! "${PURGE_LEGACY_DATA}" =~ ^(YES|NO)$ ]]; then
  echo "POKEHUNTER_PURGE_LEGACY_DELETION_DB must be YES or NO"
  exit 2
fi

case "${MODE}" in
  preflight)
    ssh "${SSH_TARGET}" 'sudo -n bash -s' <"${ROOT}/deploy/server-preflight.sh"
    ;;
  probe-ebay)
    ssh "${SSH_TARGET}" 'sudo -n python3 - --env /opt/pokehunter/.env' \
      <"${ROOT}/deploy/probe-existing-ebay.py"
    ;;
  apply)
    if [[ "${POKEHUNTER_DEPLOY_APPROVED:-}" != "YES" ]]; then
      echo "set POKEHUNTER_DEPLOY_APPROVED=YES only after explicit deployment approval"
      exit 1
    fi
    if [[ -n "$(git -C "${ROOT}" status --porcelain)" ]]; then
      echo "refusing to deploy a dirty worktree"
      exit 1
    fi
    release_id="$(git -C "${ROOT}" rev-parse --short=12 HEAD)"
    temp_dir="$(mktemp -d)"
    trap 'rm -rf "${temp_dir}"' EXIT
    git -C "${ROOT}" archive --format=tar.gz --output="${temp_dir}/${release_id}.tar.gz" HEAD
    if command -v shasum >/dev/null 2>&1; then
      artifact_sha256="$(shasum -a 256 "${temp_dir}/${release_id}.tar.gz" | awk '{print $1}')"
    elif command -v sha256sum >/dev/null 2>&1; then
      artifact_sha256="$(sha256sum "${temp_dir}/${release_id}.tar.gz" | awk '{print $1}')"
    else
      echo "missing SHA-256 utility (shasum or sha256sum)"
      exit 1
    fi
    scp "${temp_dir}/${release_id}.tar.gz" "${SSH_TARGET}:/tmp/pokehunter-${release_id}.tar.gz"
    ssh "${SSH_TARGET}" \
      "sudo -n POKEHUNTER_DEPLOY_APPROVED=YES POKEHUNTER_ENABLE_MONITOR=${ENABLE_MONITOR} POKEHUNTER_PURGE_LEGACY_DELETION_DB=${PURGE_LEGACY_DATA} bash -s -- /tmp/pokehunter-${release_id}.tar.gz ${release_id} ${artifact_sha256} ${LEGACY_UNIT}" \
      <"${ROOT}/deploy/remote-deploy.sh"
    ;;
  *)
    echo "unknown mode: ${MODE}"
    exit 2
    ;;
esac
