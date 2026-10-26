#!/usr/bin/env bash
# Install or update PokéHunter on the server. Run from the repo checkout:
#   sudo bash deploy/install.sh
# Safe to re-run after every `git pull`. It never touches the existing eBay deletion
# webhook (port 8765), Caddy, or any other service.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR=/var/lib/pokehunter
ENV_FILE=/etc/pokehunter.env
SERVICE=pokehunter-deals
PORT=8766

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run with sudo: sudo bash deploy/install.sh" >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is required (found $(python3 --version))." >&2
  exit 1
fi
if ! python3 -m venv --help >/dev/null 2>&1 || ! python3 -c 'import ensurepip' 2>/dev/null; then
  echo "Installing python3-venv..."
  apt-get update -qq && apt-get install -y -qq python3-venv
fi

id pokehunter >/dev/null 2>&1 || useradd --system --home "${DATA_DIR}" --shell /usr/sbin/nologin pokehunter
install -d -o pokehunter -g pokehunter -m 750 "${DATA_DIR}"

echo "Installing Python packages..."
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install -q --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -q "${APP_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 600 -o root -g root "${APP_DIR}/.env.example" "${ENV_FILE}"
  echo "Created ${ENV_FILE}. Fill in your keys (sudo nano ${ENV_FILE}), then run this again."
fi
chmod 600 "${ENV_FILE}"

cat >"/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=PokéHunter deal catcher (website + 15-minute eBay scanner)
After=network-online.target
Wants=network-online.target

[Service]
User=pokehunter
Group=pokehunter
WorkingDirectory=${DATA_DIR}
Environment=DATABASE_PATH=${DATA_DIR}/deals.sqlite3
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/pokehunter serve --host 127.0.0.1 --port ${PORT}
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${DATA_DIR}
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --quiet "${SERVICE}"
systemctl restart "${SERVICE}"
sleep 3
if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
  echo "PokéHunter is running on 127.0.0.1:${PORT}."
  echo "Logs: journalctl -u ${SERVICE} -f"
else
  echo "Service did not start. Check: journalctl -u ${SERVICE} -n 50" >&2
  exit 1
fi
