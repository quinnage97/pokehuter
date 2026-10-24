#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
SSH_TARGET="${2:-}"
LEGACY_UNIT="${3:-none}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "${SCRIPT_DIR}/.." rev-parse --show-toplevel)"
ENABLE_MONITOR="${POKEHUNTER_ENABLE_MONITOR:-UNCHANGED}"
PURGE_LEGACY_DATA="${POKEHUNTER_PURGE_LEGACY_DELETION_DB:-NO}"
SSH_IDENTITY="${POKEHUNTER_SSH_IDENTITY:-${HOME}/.ssh/pokehunter_deploy_ed25519}"
CONTROL_COMMAND="/usr/local/sbin/pokehunter-control"

if [[ -z "${MODE}" || -z "${SSH_TARGET}" ]]; then
  echo "usage: $0 preflight|privileged-preflight|probe-ebay|stage|apply <ssh-target> [legacy-unit.service]"
  exit 2
fi

prepare_release() {
  if [[ -n "$(git -C "${ROOT}" status --porcelain)" ]]; then
    echo "refusing to prepare a dirty worktree"
    exit 1
  fi
  release_id="$(git -C "${ROOT}" rev-parse --short=12 HEAD)"
  temp_dir="$(mktemp -d)"
  artifact="${temp_dir}/${release_id}.tar.gz"
  git -C "${ROOT}" archive --format=tar.gz --output="${artifact}" HEAD
  if command -v shasum >/dev/null 2>&1; then
    artifact_sha256="$(shasum -a 256 "${artifact}" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    artifact_sha256="$(sha256sum "${artifact}" | awk '{print $1}')"
  else
    echo "missing SHA-256 utility (shasum or sha256sum)"
    exit 1
  fi
}
if [[ ! -f "${SSH_IDENTITY}" ]]; then
  echo "missing dedicated SSH identity: ${SSH_IDENTITY}"
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
    # Safe before the root bootstrap is installed. Privileged fields are reported as unavailable.
    ssh -i "${SSH_IDENTITY}" -o IdentitiesOnly=yes "${SSH_TARGET}" 'bash -s' \
      <"${ROOT}/deploy/server-preflight.sh"
    ;;
  privileged-preflight)
    ssh -i "${SSH_IDENTITY}" -o IdentitiesOnly=yes "${SSH_TARGET}" \
      "sudo -n ${CONTROL_COMMAND} preflight"
    ;;
  probe-ebay)
    ssh -i "${SSH_IDENTITY}" -o IdentitiesOnly=yes "${SSH_TARGET}" \
      "sudo -n ${CONTROL_COMMAND} probe-ebay"
    ;;
  stage)
    if [[ "${POKEHUNTER_STAGE_APPROVED:-}" != "YES" ]]; then
      echo "set POKEHUNTER_STAGE_APPROVED=YES only after explicit artifact-staging approval"
      exit 1
    fi
    prepare_release
    trap 'rm -rf "${temp_dir}"' EXIT
    scp -i "${SSH_IDENTITY}" -o IdentitiesOnly=yes \
      "${artifact}" "${SSH_TARGET}:/tmp/pokehunter-${release_id}.tar.gz"
    echo "release_id=${release_id}"
    echo "artifact_sha256=${artifact_sha256}"
    echo "administrator_authorization_command:"
    echo "sudo env POKEHUNTER_RELEASE_APPROVED=YES /usr/local/libexec/pokehunter/authorize-release-root.sh /tmp/pokehunter-${release_id}.tar.gz ${release_id} ${artifact_sha256} ${LEGACY_UNIT} ${ENABLE_MONITOR} ${PURGE_LEGACY_DATA}"
    ;;
  apply)
    if [[ "${POKEHUNTER_DEPLOY_APPROVED:-}" != "YES" ]]; then
      echo "set POKEHUNTER_DEPLOY_APPROVED=YES only after explicit deployment approval"
      exit 1
    fi
    prepare_release
    trap 'rm -rf "${temp_dir}"' EXIT
    scp -i "${SSH_IDENTITY}" -o IdentitiesOnly=yes \
      "${artifact}" "${SSH_TARGET}:/tmp/pokehunter-${release_id}.tar.gz"
    # All client-side expansions below are constrained by strict allowlists or Git output.
    # shellcheck disable=SC2029
    ssh -i "${SSH_IDENTITY}" -o IdentitiesOnly=yes "${SSH_TARGET}" \
      "sudo -n ${CONTROL_COMMAND} apply /tmp/pokehunter-${release_id}.tar.gz ${release_id} ${artifact_sha256} ${LEGACY_UNIT} ${ENABLE_MONITOR} ${PURGE_LEGACY_DATA}"
    ;;
  *)
    echo "unknown mode: ${MODE}"
    exit 2
    ;;
esac
