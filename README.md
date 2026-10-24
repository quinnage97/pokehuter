# PokéHunter

PokéHunter discovers English Charizard and Mew cards on eBay UK, estimates a fair
asking price from similar live listings, scores bargains, and sends deduplicated Telegram
alerts. It also exposes the eBay Marketplace Account Deletion endpoint required for eBay
developer compliance.

The project is intentionally isolated from other services on the host. Its supplied systemd
units use the `pokehunter` user, `/opt/pokehunter`, `/var/lib/pokehunter`, port `8088`, and
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

- `GET /webhooks/ebay/account-deletion?challenge_code=...` returns the required SHA-256
  `challengeResponse` JSON.
- `POST /webhooks/ebay/account-deletion` requires JSON and a valid `X-EBAY-SIGNATURE`.
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
| `EBAY_DELIVERY_POSTCODE` | Recommended destination postcode for accurate calculated shipping |
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

See [docs/deployment.md](docs/deployment.md). Deployment, eBay portal submission, and any
reverse-proxy change are intentionally manual approval gates.
