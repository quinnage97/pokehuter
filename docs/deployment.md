# Hetzner Ubuntu deployment

These instructions are a runbook only. Nothing in this repository deploys automatically.

## Safety boundary: Hermes

Hermes AI Command Centre shares the server and must not be disrupted. Before deployment, use
read-only inspection (`systemctl list-units`, `ss -ltnp`, and the active reverse-proxy config) to
confirm that port `8088`, user `pokehunter`, directories `/opt/pokehunter` and
`/var/lib/pokehunter`, and the `pokehunter-*` unit names are unused. If any conflict exists,
stop and choose new PokéHunter-only names. Never restart, reload, edit, or stop a Hermes unit,
process, container, file, directory, port, or database.

Only the existing reverse proxy may need a narrowly scoped route addition. Inspect and validate
its configuration before changing it. A proxy reload should occur only after explicit approval
and only if its validation command passes; do not restart Hermes.

## 1. Install in isolated paths

Run as an administrator after reviewing every command:

```bash
sudo useradd --system --home /var/lib/pokehunter --shell /usr/sbin/nologin pokehunter
sudo install -d -o pokehunter -g pokehunter -m 0750 /opt/pokehunter /var/lib/pokehunter
sudo git clone <REPOSITORY_URL> /opt/pokehunter
cd /opt/pokehunter
sudo -u pokehunter python3 -m venv .venv
sudo -u pokehunter .venv/bin/pip install --require-virtualenv .
sudo install -o root -g pokehunter -m 0640 .env.example /etc/pokehunter.env
```

Edit `/etc/pokehunter.env` as root. Set `DATABASE_PATH=/var/lib/pokehunter/pokehunter.sqlite3`
and real credentials. Do not put secrets in the repository or systemd unit.

## 2. Install systemd units

```bash
sudo install -o root -g root -m 0644 deploy/systemd/pokehunter-web.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/pokehunter-monitor.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/pokehunter-monitor.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/pokehunter-*.service /etc/systemd/system/pokehunter-monitor.timer
sudo systemctl daemon-reload
sudo systemctl enable --now pokehunter-web.service pokehunter-monitor.timer
```

The timer runs every five minutes with jitter. `flock` prevents overlapping monitor runs.

## 3. Verify locally

```bash
systemctl status pokehunter-web.service pokehunter-monitor.timer
curl --fail --silent http://127.0.0.1:8088/healthz
journalctl -u pokehunter-web.service -u pokehunter-monitor.service --since today
```

Trigger one monitored run only after credentials are configured:

```bash
sudo systemctl start pokehunter-monitor.service
```

## 4. Add the HTTPS route

Route only `/webhooks/ebay/account-deletion` to `http://127.0.0.1:8088` in the server's
existing HTTPS reverse proxy. Also route `/healthz` only if it is intentionally protected or
needed externally. Do not replace the existing virtual host or catch-all, and do not touch
Hermes routes.

After validating the proxy configuration, obtain explicit approval before reloading it. Confirm
the public endpoint returns JSON for a test challenge and uses the exact URL in
`EBAY_DELETION_ENDPOINT`.

## 5. eBay compliance approval gate

Do **not** enter or save the endpoint/token in eBay's developer portal until the owner approves.
After approval, enter the exact endpoint and token, let eBay perform its GET challenge, then use
"Send Test Notification" and confirm a `204` plus an `account_deletion_acknowledged` log event.
Never paste notification payloads or account identifiers into tickets or logs.

## Operations and rollback

Application logs are structured JSON in journald:

```bash
journalctl -u pokehunter-web.service -u pokehunter-monitor.service -f
```

To roll back PokéHunter, stop and disable only `pokehunter-web.service` and
`pokehunter-monitor.timer`, then restore the prior PokéHunter release. Do not issue broad
container, proxy, or system-wide restart commands.
