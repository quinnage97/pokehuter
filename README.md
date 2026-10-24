# PokéHunter

PokéHunter discovers English Charizard and Mew cards on eBay UK, estimates a fair
asking price from similar live listings, scores bargains, and sends deduplicated Telegram
alerts. It also exposes the eBay Marketplace Account Deletion endpoint required for eBay
developer compliance.

The project is intentionally isolated from other services on the host. Its supplied systemd
units use the `pokehunter` user, `/opt/pokehunter`, `/var/lib/pokehunter`, port `8765`, and
service names prefixed with `pokehunter-`. They do not read, write, restart, or otherwise
interact with Hermes AI Command Centre.

## How pricing works

For each configured search, PokéHunter:

1. Gets up to 100 fixed-price listings from eBay's Browse API. Auctions are excluded because a
   current bid is not a reliable purchase price.
2. Rejects wrong-currency, non-target, replica/custom/lot, and non-English listings.
3. Compares a listing only with listings having the same grading bucket and sufficiently
   similar normalized title tokens.
4. Uses the median delivered asking price (item plus shipping) of at least three comparable
   listings.
5. Scores discount depth, comparable count, and price percentile, then applies the configured
   minimum discount, saving, and score.
6. Sends an alert once per eBay item ID. A failed send is recorded without marking the item as
   alerted, so it can retry on the next run.

Eligible alerts retain eBay's returned Best Match order; PokéHunter does not re-sort Browse
results. The bargain score is used only as a threshold and as explanatory alert metadata. The
production-access application must accurately disclose this filtering and comparison use case.

The estimate is based on **current asking prices, not completed-sale prices**. Alerts say this
explicitly. Authenticity and condition still require a human check.

## Local setup

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
```

Fill `.env` with eBay application credentials, Telegram bot details, and webhook values. `.env`
is ignored by Git. Do not place credentials in source, unit files, command history, or logs.

Run one monitoring cycle:

```bash
.venv/bin/pokehunter monitor
```

Validate external access without writing scanner state or sending a message:

```bash
.venv/bin/pokehunter check-ebay
.venv/bin/pokehunter check-telegram
```

The eBay check also exercises English filtering, comparisons, and bargain thresholds. It stores
nothing and sends no alerts. The Telegram check uses only read-only bot methods.

Run the webhook locally (the eBay portal still requires a public HTTPS endpoint):

```bash
.venv/bin/pokehunter serve --host 127.0.0.1 --port 8088
```

Run quality checks:

```bash
.venv/bin/ruff check .
.venv/bin/pytest --cov=pokehunter --cov-report=term-missing
```

## eBay deletion webhook

Configure the exact public URL, including path and any trailing slash, as
`EBAY_DELETION_ENDPOINT`. The value must exactly match the URL entered in eBay's portal. Set
`EBAY_VERIFICATION_TOKEN` to the same 32–80 character token entered there.

- `GET /ebay/deletion?challenge_code=...` returns the required SHA-256
  `challengeResponse` JSON.
- `POST /ebay/deletion` requires JSON and a valid `X-EBAY-SIGNATURE`. The longer
  `/webhooks/ebay/account-deletion` path is retained as an alias.
- eBay public keys are fetched using app OAuth and cached for one hour.
- Valid deletion messages receive `204 No Content`; invalid signatures receive `412`.
- PokéHunter does not store usernames, public user IDs, EIAS tokens, seller details, or deletion
  payloads. Only a one-way short hash of the notification ID is written to structured logs for
  operational correlation.

Do not submit the endpoint in the eBay portal until it is deployed behind HTTPS and a test GET
through the public URL succeeds. The official references are the
[Marketplace Account Deletion guide](https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion)
and [Notification API](https://developer.ebay.com/develop/api/buy/notification_api).

## Configuration

All configuration is via environment variables; see `.env.example`.

Production Browse API use requires eBay approval. eBay currently directs production Buy API
applicants through the eBay Partner Network; without approved production access, discovery will
fail closed and no alerts will be sent. Sandbox mode is available for integration checks, but its
inventory is synthetic.

| Variable | Purpose |
| --- | --- |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | eBay production or sandbox app credentials |
| `EBAY_ENVIRONMENT` | `production` or `sandbox` |
| `EBAY_MARKETPLACE_ID` | Marketplace; production deployment is fixed to `EBAY_GB` |
| `EBAY_DELIVERY_COUNTRY` | Delivery country; production deployment uses `GB` |
| `EBAY_DELIVERY_POSTCODE` | Configurable UK destination postcode for calculated shipping |
| `EBAY_AFFILIATE_CAMPAIGN_ID` | 10-digit EPN campaign ID for production affiliate tracking |
| `EBAY_AFFILIATE_REFERENCE_ID` | Optional EPN reference ID, maximum 256 characters |
| `EBAY_DELETION_ENDPOINT` | Exact public HTTPS webhook URL |
| `EBAY_VERIFICATION_TOKEN` | Portal token, 32–80 allowed characters |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Alert destination |
| `DATABASE_PATH` | SQLite state file |
| `MIN_DISCOUNT_PERCENT` | Minimum estimated discount, default 20% |
| `MIN_SAVINGS` | Minimum delivered-price saving, default GBP 10 |
| `MIN_BARGAIN_SCORE` | Minimum score, default 60/100 |
| `MIN_COMPARABLES` | Minimum like-for-like comparison count, default 3 |
| `REQUIRE_ENGLISH` | Strict English aspect/title filter, default true |

With strict English filtering, listings without an English language aspect or the word
"English" in the title are not alerted. This favors precision over recall.

## Production deployment

See [docs/deployment.md](docs/deployment.md). The prepared deployment preserves the existing
public URL, Caddy route, and local port `8765`; deployment, monitor activation, and eBay portal
submission are separate manual approval gates.
