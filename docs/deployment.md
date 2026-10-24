# Hetzner Ubuntu deployment runbook

This runbook preserves the live eBay endpoint at
`https://pokehunter-nq.duckdns.org/ebay/deletion` and the existing local listener port `8765`.
The deployment does not edit or reload Caddy. It never targets a Hermes unit, process, container,
directory, port, or database.

Nothing deploys automatically. The apply path requires the owner to provide an explicit approval
environment variable after reviewing a read-only preflight.

## Safety and approval boundaries

- `deploy/hetzner-deploy.sh preflight` is read-only. It inventories only relevant services,
  listeners, containers, Caddy validation state, configuration key names, and the public challenge
  response. It never prints configuration values.
- `apply` refuses to run unless `POKEHUNTER_DEPLOY_APPROVED=YES` is present and the local worktree
  is clean.
- The monitor timer remains disabled unless the separate
  `POKEHUNTER_ENABLE_MONITOR=YES` approval flag is present.
- The deployment fingerprints Hermes-related units and containers before and after. Any change
  causes PokéHunter rollback.
- Do not submit or modify the eBay Marketplace Account Deletion form as part of deployment.

## 1. Read-only server preflight

Use a sudo-capable SSH account. This command performs no server mutation:

```bash
bash deploy/hetzner-deploy.sh preflight <ssh-target>
```

Confirm that the output identifies the current legacy PokéHunter/eBay webhook service listening on
`127.0.0.1:8765`, reports the public webhook healthy, and reports Caddy's configuration valid.
Record the exact legacy systemd unit name; the apply script will refuse to stop any unit unless its
`ExecStart` identifies it as PokéHunter or `ebay_notifications.py`. If the listener cannot be tied
to such a unit, stop and inspect it manually.

## 2. Prepare root-owned configuration

Create the dedicated account before writing the configuration:

```bash
sudo useradd --system --home /var/lib/pokehunter --shell /usr/sbin/nologin pokehunter
sudo install -d -o pokehunter -g pokehunter -m 0750 /var/lib/pokehunter
```

The migration helper reads legacy values on the server, never displays them, and is dry-run by
default:

```bash
sudo python3 deploy/migrate-config.py
```

It prints only missing key names. After filling missing values in the legacy source or a temporary
root-only source file, explicitly authorize the one-time write:

```bash
sudo POKEHUNTER_CONFIG_MIGRATION_APPROVED=YES python3 deploy/migrate-config.py --write
```

Alternatively create `/etc/pokehunter.env` manually as `root:pokehunter` with mode `0640`, using
`.env.example` as the key list. Do not paste secrets into shell arguments, terminal output, Git, or
this runbook. Required production values include:

- eBay production client ID and secret;
- the existing 32–80 character eBay verification token;
- exact endpoint `https://pokehunter-nq.duckdns.org/ebay/deletion`;
- Telegram bot token and target chat ID;
- `DATABASE_PATH=/var/lib/pokehunter/pokehunter.sqlite3`;
- the GB delivery postcode; and
- the 10-digit EPN campaign ID if the application's production Browse access is tied to EPN.

## 3. Permission checks

The deployment builds an isolated release and runs both checks before changing the live symlink or
stopping the legacy webhook:

```text
pokehunter check-ebay
pokehunter check-telegram
```

`check-ebay` obtains an application token and performs the configured Browse searches. It does not
send alerts, write the database, or print tokens or listing details. A successful result proves the
credentials can call Browse in the configured environment; it does not replace confirmation that
eBay approved this production bargain-ranking/alert use case or any required EPN agreement.

`check-telegram` calls only `getMe` and `getChat`; it sends no message.

## 4. Approval-gated release

Do not run this command until the owner explicitly approves deployment. Pass the exact legacy unit
name found during preflight:

```bash
POKEHUNTER_DEPLOY_APPROVED=YES \
  bash deploy/hetzner-deploy.sh apply <ssh-target> <legacy-unit.service>
```

The script creates an immutable release under `/opt/pokehunter/releases/<commit>`, installs pinned
top-level dependencies into that release, validates the systemd units, runs both permission checks,
and atomically switches `/opt/pokehunter/current`. It stops only the validated legacy PokéHunter
unit and starts only `pokehunter-web.service`. The existing Caddy route and port remain unchanged.

After local and public webhook verification passes, the old unit is disabled. Any failure after
activation restores the previous symlink and restarts the validated legacy unit. Release files are
left in place for diagnosis; no broad cleanup or restart is performed.

## 5. Separately enable scheduled monitoring

Enabling alerts is a separate operational decision. After reviewing a manual monitor result and
logs, an administrator may explicitly enable only `pokehunter-monitor.timer` after approval:

```bash
sudo systemctl enable --now pokehunter-monitor.timer
```

For a first deployment where monitoring has already received separate approval, set
`POKEHUNTER_ENABLE_MONITOR=YES` alongside `POKEHUNTER_DEPLOY_APPROVED=YES`. The timer uses `flock`
to prevent overlapping runs.

## Verification and operations

The automated release verifier checks the atomic release target, the new web unit, local health,
and the existing public challenge endpoint. Useful read-only follow-up commands are:

```bash
systemctl status pokehunter-web.service pokehunter-monitor.timer
journalctl -u pokehunter-web.service -u pokehunter-monitor.service --since today
curl --fail --silent http://127.0.0.1:8765/healthz
```

Never restart or reload Caddy or Hermes to operate PokéHunter. Never log eBay deletion payloads,
account identifiers, tokens, or Telegram credentials.
