# Hetzner Ubuntu deployment runbook

This runbook preserves the live eBay endpoint at
`https://pokehunter-nq.duckdns.org/ebay/deletion` and the existing local listener port `8765`.
The deployment does not edit or reload Caddy. It never invokes Docker or targets a Hermes unit,
process, container, directory, port, or database.

Nothing deploys automatically. The apply path requires the owner to provide an explicit approval
environment variable after reviewing a read-only preflight.

The SSH identity is installed only for a dedicated `pokehunter-deploy` account. Do not add this
account to `sudo`, `adm`, `docker`, `lxd`, or any application group. Its standing elevation is
limited to two exact root-owned `/usr/local/sbin/pokehunter-control` commands: read-only preflight
and the read-only eBay permission probe. Both are tagged `NOSETENV`; the Python dispatcher also
replaces the entire environment before executing a validated root-owned helper. It does not accept
shell commands, arbitrary paths, or arbitrary programs.

## Safety and approval boundaries

- `deploy/hetzner-deploy.sh preflight` runs without sudo and is safe immediately after the key is
  installed. `privileged-preflight` becomes available only after the separately approved bootstrap.
- The bootstrap installs fixed root-owned helpers, fixed systemd units, and one narrow sudoers
  rule. It does not deploy a release, start a service, touch Caddy or Hermes, migrate configuration,
  or delete data.
- `apply` refuses to run unless `POKEHUNTER_DEPLOY_APPROVED=YES` is present and the local worktree
  is clean.
- The monitor timer remains disabled unless the separate
  `POKEHUNTER_ENABLE_MONITOR=YES` approval flag is present on its first deployment. Later upgrades
  preserve its existing state unless `YES` or `NO` is explicitly selected.
- If the legacy deletion database exists, apply refuses to proceed unless the separate destructive
  cleanup flag `POKEHUNTER_PURGE_LEGACY_DELETION_DB=YES` is present.
- The deployment fingerprints visible Hermes-related systemd units before and after. Any change
  causes PokéHunter rollback. It deliberately does not inspect Docker; the account has no Docker
  group membership and no helper invokes the Docker API.
- Do not submit or modify the eBay Marketplace Account Deletion form as part of deployment.
- The production scanner is blocked until eBay production Browse access is confirmed by the
  read-only `probe-ebay` and release `check-ebay` checks.

## 1. Dedicated deployment identity and read-only preflight

Install the supplied public key for a separate `pokehunter-deploy` account with the OpenSSH
`restrict` option. Do not grant it general sudo access. The client uses the dedicated private key
at `~/.ssh/pokehunter_deploy_ed25519` by default; override only with
`POKEHUNTER_SSH_IDENTITY=/absolute/path`.

This command performs no server mutation and does not use sudo:

```bash
bash deploy/hetzner-deploy.sh preflight <ssh-target>
```

It can confirm SSH, OS, Python, the listener, visible service state, disk space, and the public
webhook. Fields requiring root access are reported as unavailable. It never reads secrets.

## 2. Separately approved root bootstrap

Do not run the bootstrap merely to perform the initial read-only preflight. After its exact commit
has been reviewed and the owner explicitly approves this server change, a current administrator
may run it from a root-owned copy beneath a root-owned, non-group/world-writable path. The script
refuses sources that are symlinks, non-regular, non-root-owned, or group/world-writable:

```bash
sudo POKEHUNTER_BOOTSTRAP_APPROVED=YES bash deploy/bootstrap-root.sh
```

The bootstrap creates two unprivileged runtime accounts and a separate non-login build account,
installs root-owned helpers under
`/usr/local/libexec/pokehunter`, installs the three fixed PokéHunter units without enabling,
starting, or reloading systemd, and validates `/etc/sudoers.d/pokehunter-deploy`. Its complete
standing sudo policy is:

```text
pokehunter-deploy ALL=(root) NOPASSWD:NOSETENV: /usr/local/sbin/pokehunter-control preflight
pokehunter-deploy ALL=(root) NOPASSWD:NOSETENV: /usr/local/sbin/pokehunter-control probe-ebay
```

There is no standing `apply` permission. After a separate deployment approval, an administrator
must run the root-only `authorize-release-root.sh` helper with the exact artifact path, commit,
SHA-256, legacy unit, monitor choice, and purge choice. That creates a temporary sudoers entry for
only that exact invocation. The deployment consumes the entry before cutover, so a failure or
replay requires fresh administrator authorization.

The root-owned dispatcher validates a finite argument set before calling only root-owned helpers.
It never runs a script or systemd unit from the uploaded release as root. Package build hooks and
archive extraction run as the non-login `pokehunter-build` account, which is separate from the SSH
deployment identity. The app later runs as the separate `pokehunter` and `pokehunter-web` users.
The bootstrap refuses if the deployment account belongs to `sudo`, `adm`, `docker`, or `lxd`.
Confirm the account has no other sudo privileges with:

```bash
sudo -l -U pokehunter-deploy
```

Then run the complete privileged inventory:

```bash
bash deploy/hetzner-deploy.sh privileged-preflight <ssh-target>
```

Confirm that it identifies the current legacy PokéHunter/eBay webhook service on
`127.0.0.1:8765`, reports the public webhook healthy, and reports Caddy's configuration valid.
Record the exact legacy unit name. Apply refuses to stop a unit unless its `ExecStart` identifies
it as PokéHunter or `ebay_notifications.py` and its process owns port 8765.

The preflight also reports whether `/opt/pokehunter/data/ebay_deletions.sqlite3` exists. The legacy
implementation stores eBay usernames and user IDs there. Unless there is a documented legal
retention requirement, this personal data should be deleted at a separately approved cutover.

Check the existing production keyset without writing server files or showing credentials:

```bash
bash deploy/hetzner-deploy.sh probe-ebay <ssh-target>
```

This reads the legacy `/opt/pokehunter/.env` in memory and reports only OAuth/Browse HTTP status,
the number of returned summaries, and whether the EPN campaign and delivery postcode are present.
It is not a substitute for explicit eBay production-access confirmation. A failed or unconfirmed
production probe blocks deployment.

## 3. Prepare root-owned configuration

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

Alternatively create `/etc/pokehunter.env` manually as `root:root` with mode `0600`, using
`.env.example` as the key list. Do not paste secrets into shell arguments, terminal output, Git, or
this runbook. Required production values include:

- eBay production client ID and secret;
- the existing 32–80 character eBay verification token;
- exact endpoint `https://pokehunter-nq.duckdns.org/ebay/deletion`;
- Telegram bot token and target chat ID;
- `DATABASE_PATH=/var/lib/pokehunter/pokehunter.sqlite3`;
- `EBAY_MARKETPLACE_ID=EBAY_GB`, `EBAY_CURRENCY=GBP`, and `EBAY_DELIVERY_COUNTRY=GB`;
- a configurable `EBAY_DELIVERY_POSTCODE` for the intended delivery area; and
- the 10-digit EPN campaign ID required for production Browse use.

## 4. Permission checks

The deployment builds an isolated release and runs both checks before changing the live symlink or
stopping the legacy webhook:

```text
pokehunter check-ebay
pokehunter check-telegram
```

`check-ebay` obtains an application token, performs the configured Browse searches, and exercises
English filtering, like-for-like comparisons, and bargain thresholds. It does not send alerts,
write the database, or print tokens or listing details. A successful result proves the credentials
can call Browse and the scanner can consume current results in the configured environment; it does
not replace confirmation that eBay approved this production bargain-ranking/alert use case or any
required EPN agreement.

`check-telegram` calls only `getMe` and `getChat`; it sends no message.

## 5. Approval-gated release

Do not authorize or run these commands until the owner explicitly approves deployment. First build
and upload the clean Git archive with the non-privileged `stage` action, then have an existing
administrator run the exact authorization command it prints. Pass the exact legacy unit found
during preflight and the separately approved monitor/purge choices:

```bash
POKEHUNTER_STAGE_APPROVED=YES \
POKEHUNTER_PURGE_LEGACY_DELETION_DB=YES \
  bash deploy/hetzner-deploy.sh stage <ssh-target> <legacy-unit.service>
```

The stage action only uploads `/tmp/pokehunter-<commit>.tar.gz`; it does not invoke sudo or change
services. Its output includes the exact release and checksum in this administrator command:

```bash
sudo POKEHUNTER_RELEASE_APPROVED=YES \
  /usr/local/libexec/pokehunter/authorize-release-root.sh \
  /tmp/pokehunter-<commit>.tar.gz <commit> <sha256> \
  <legacy-unit.service> UNCHANGED YES
```

Then run the matching apply command. If preflight reports the legacy deletion database, the owner
must also explicitly approve its irreversible deletion:

```bash
POKEHUNTER_DEPLOY_APPROVED=YES \
POKEHUNTER_PURGE_LEGACY_DELETION_DB=YES \
  bash deploy/hetzner-deploy.sh apply <ssh-target> <legacy-unit.service>
```

The script uploads a checksum-verified Git archive and invokes only the fixed root-owned control
command. It creates an immutable release under `/opt/pokehunter/releases/<commit>`, installs pinned
top-level dependencies as the unprivileged deployment account, validates the fixed root-owned
systemd units, runs both permission checks, and atomically switches `/opt/pokehunter/current`. It
stops only the validated legacy PokéHunter unit and starts only `pokehunter-web.service`. The
existing Caddy route and port remain unchanged.
The public webhook and monitor run under different unprivileged accounts with bounded memory,
task counts, and low CPU/I/O weights, so the scanner cannot contend aggressively with Hermes.
The release must reproduce the legacy server's challenge hash for the same unique challenge,
proving that the endpoint and verification token remained continuous; otherwise it rolls back.

After local and public webhook verification passes, the old unit is disabled. Any failure after
activation restores the previous symlink and restarts the validated legacy unit. Release files are
left in place for diagnosis; no broad cleanup or restart is performed. When separately approved,
only the exact legacy SQLite database and its SQLite sidecar files are deleted after verification.
The purge first refuses any database with an unexpected table or deletion schema, then enables
SQLite secure deletion, deletes the rows, vacuums the file, and unlinks only the exact legacy file
and its SQLite sidecars. It never traverses directories, deletes unrelated data, or modifies any
Hetzner snapshot or backup. Existing backups remain untouched and should follow the owner's normal
retention policy; do not delete an entire backup merely to remove this database.

## 6. Separately enable scheduled monitoring

Enabling alerts is a separate operational decision and must wait for confirmed production eBay
access. After reviewing a manual monitor result and logs, an administrator may explicitly enable
only `pokehunter-monitor.timer` after approval:

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
