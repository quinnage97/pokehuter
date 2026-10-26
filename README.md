# PokéHunter: Charizard & Mew deal catcher

Every 15 minutes PokéHunter searches eBay UK for Charizard and Mew cards. It works out
exactly which card each listing is (set, number, 1st Edition / holo / reverse holo) and its
condition (NM / LP / MP / HP / damaged). Then it compares the delivered price with the
card's market price, adjusted for condition. Good deals show on the website and are sent to
Telegram.

- **Checklist**: every English Charizard and Mew card, one tick box per version (a master
  set needs 1st Edition, holo, reverse holo…). Ticked cards stop being searched and alerted.
- **⭐ Priority**: starred cards are searched by name every scan, alert at a smaller discount
  (5% by default), and appear in "Priority watch" with the cheapest copy currently listed.
- **Alert at £**: set your own price on any card. You'll be told whenever a copy is listed
  at or below it.
- **Played copies are welcome**: an LP/MP/HP copy is judged against what that condition is
  worth (NM 100%, LP 85%, MP 70%, HP 55%). MP/HP are only considered for cards worth more
  than £15 (adjustable), so cheap cards aren't bought battered.

## How a "deal" is decided

1. **Card list & prices** come from [TCGdex](https://tcgdex.dev) (free, no key), refreshed
   daily. Cardmarket (EU) prices are used by default because they track UK prices more closely
   than TCGplayer (US). Both are converted to GBP at the day's ECB rate.
2. **eBay search** (Browse API, Buy It Now only): one "newly listed" sweep per Pokémon, plus
   about 30 card-by-card searches per scan. Starred cards go first, then whichever was
   searched longest ago. That's about 3,000 calls a day, inside eBay's free 5,000.
3. **Matching** needs the card number in the title (e.g. `4/102`, `TG03/TG30`, `SWSH050`).
   Graded, non-English, proxy/custom, lots, "choose your card" multi-listings and similar are
   skipped. When a title is ambiguous it is valued as the *cheaper* card, so it can't create a
   false alarm.
4. **Deal** = delivered price at least 20% under the condition-adjusted price (and at least £2
   saved), from a seller with 5+ feedback. Everything is adjustable on the Settings tab.

Prices are a guide. Always look at the photos before buying, especially when a listing is
flagged "much cheaper than usual".

## Setting it up on the Hetzner server

You need: the eBay **Production** Client ID + Client Secret, a Telegram bot token and chat
id, and SSH access to the server.

```bash
# 1. Get the code (one time)
sudo git clone https://github.com/quinnage97/pokehuter.git /opt/pokehunter-app
cd /opt/pokehunter-app

# 2. Install. The first run creates /etc/pokehunter.env for your keys.
sudo bash deploy/install.sh
sudo nano /etc/pokehunter.env        # fill in eBay, Telegram, SITE_PASSWORD
sudo bash deploy/install.sh          # run again to start it

# 3. Check eBay + Telegram work (sends a test Telegram message)
sudo /opt/pokehunter-app/.venv/bin/pokehunter --env-file /etc/pokehunter.env check
```

Then put it on the web. Edit `/etc/caddy/Caddyfile` to match `deploy/Caddyfile.example`.
That keeps `/ebay/*` going to the existing eBay deletion webhook on port 8765 and sends
everything else to the site on port 8766. Then:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Open https://pokehunter-nq.duckdns.org/ and log in with any username and your
`SITE_PASSWORD`. The first card download takes a minute or two. The first eBay scan starts
straight after.

**Updating** after changes are pushed to GitHub:

```bash
cd /opt/pokehunter-app && sudo git pull && sudo bash deploy/install.sh
```

**Logs**: `journalctl -u pokehunter-deals -f`

## Server settings (`/etc/pokehunter.env`)

| Variable | What it is |
| --- | --- |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | eBay developer portal → Application Keys → Production |
| `EBAY_DELIVERY_POSTCODE` | Optional, makes postage costs accurate |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | From @BotFather and @userinfobot |
| `SITE_PASSWORD` | Password for the website |
| `PUBLIC_URL` | Link added to alerts |
| `SCAN_INTERVAL_MINUTES` | Default 15 |
| `POKEMON` | Default `Charizard,Mew`. Add more, comma separated |
| `EBAY_DAILY_CALL_BUDGET` | Default 4500 (eBay's limit is 5000) |

Everything else (discounts, conditions, alerts, price source) is on the website's Settings tab.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pokehunter serve    # http://127.0.0.1:8766, reads ./.env
```

`app/ebay_notifications.py` is the eBay Marketplace Account Deletion webhook that is already
running on the server (port 8765). eBay requires it for API access. It's left untouched.
