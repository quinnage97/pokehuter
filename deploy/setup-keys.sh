#!/usr/bin/env bash
# Fill in /etc/pokehunter.env without typing symbols (the Hetzner web console mangles them).
# Copies keys the old setup already stored on this server, asks for anything still missing,
# then restarts PokéHunter and tests eBay + Telegram. Run as root:
#   bash /opt/pokehunter-app/deploy/setup-keys.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${POKEHUNTER_ENV_FILE:-/etc/pokehunter.env}"
OLD_DIR="${POKEHUNTER_OLD_DIR:-/home/commandadmin/pokehunter}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this as root." >&2
  exit 1
fi
[[ -f "${ENV_FILE}" ]] || install -m 600 -o root -g root "${APP_DIR}/.env.example" "${ENV_FILE}"

# Last non-empty value of KEY in a KEY=value file, without surrounding quotes.
read_key() {
  local file="$1" key="$2"
  [[ -r "${file}" ]] || return 0
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "${file}" \
    | sed "s/^[\"']//; s/[\"'][[:space:]]*\$//" | grep -v '^[[:space:]]*$' | tail -n 1 || true
}

set_key() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -v "^[[:space:]]*${key}[[:space:]]*=" "${ENV_FILE}" >"${tmp}" || true
  printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
  install -m 600 -o root -g root "${tmp}" "${ENV_FILE}"
  rm -f "${tmp}"
}

# Look for any of the given key names in the old PokéHunter folder.
find_old() {
  local key value file
  [[ -d "${OLD_DIR}" ]] || return 0
  for key in "$@"; do
    value="$(read_key "${OLD_DIR}/.env" "${key}")"
    if [[ -n "${value}" ]]; then echo "${value}"; return 0; fi
  done
  while IFS= read -r file; do
    for key in "$@"; do
      value="$(read_key "${file}" "${key}")"
      if [[ -n "${value}" ]]; then echo "${value}"; return 0; fi
    done
  done < <(grep -rlsE --exclude-dir=.venv --exclude-dir=venv --exclude-dir=.git \
    --exclude-dir=node_modules "^[[:space:]]*($(IFS='|'; echo "$*"))[[:space:]]*=" "${OLD_DIR}" \
    | head -n 20)
}

# ensure KEY "Question" [aliases...]
ensure() {
  local key="$1" question="$2" value
  shift 2
  if [[ -n "$(read_key "${ENV_FILE}" "${key}")" ]]; then
    echo "OK      ${key} already set"
    return 0
  fi
  value="$(find_old "${key}" "$@")"
  if [[ -n "${value}" ]]; then
    set_key "${key}" "${value}"
    echo "COPIED  ${key} from the old setup"
    return 0
  fi
  echo
  read -r -p "${question} (or just press Enter to skip): " value
  if [[ "${key}" == "TELEGRAM_BOT_TOKEN" ]]; then
    value="${value/;/:}"  # the console often types ; instead of :
  fi
  if [[ -n "${value}" ]]; then
    set_key "${key}" "${value}"
    echo "SAVED   ${key}"
  else
    echo "SKIPPED ${key}"
  fi
}

echo "Setting up ${ENV_FILE}"
ensure EBAY_CLIENT_ID "eBay Production App ID (Client ID)" EBAY_APP_ID EBAY_CLIENTID
ensure EBAY_CLIENT_SECRET "eBay Production Cert ID (Client Secret)" EBAY_CERT_ID EBAY_CLIENTSECRET
ensure TELEGRAM_CHAT_ID "Telegram chat id (the number from @userinfobot)"
ensure TELEGRAM_BOT_TOKEN "Telegram bot token from @BotFather"
ensure SITE_PASSWORD "Choose a website password (letters and numbers only)"

echo
bash "${APP_DIR}/deploy/install.sh"
echo
"${APP_DIR}/.venv/bin/pokehunter" --env-file "${ENV_FILE}" check || true
