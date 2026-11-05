# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

WsToDiscord monitors two Japanese Weiss Schwarz card shops (Hobbystation and Fukufuku) for inventory changes and sends Discord notifications. It has two entry points with different deployment models.

## Environment Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
```

Required env vars: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`
For `bot.py` mode also: `DISCORD_GUILD_ID`

## Running

**Scraper-only mode** (runs once or on schedule via APScheduler):
```bash
python main.py                # run once immediately, then every 30 min
python main.py --force-notify # treat all products as new (useful for testing embeds)
```

**Persistent Discord bot mode** (slash commands + scheduled scraping):
```bash
python bot.py
```

**Docker:**
```bash
docker build -t ws-discord .
docker run --env-file .env -e DEPLOYMENT_ENV=local ws-discord
```

## Testing Scrapers

```bash
python -c "from scrapers.hobbystation import scrape; p=scrape(); print(len(p), p[:2])"
python -c "from scrapers.fukufuku import scrape; p=scrape(); print(len(p), p[:2])"
```

Test price change detection: run once to populate `state.json`, manually edit a `price_int`/`price_raw` value, then run again.

## Architecture

### Two Entry Points

- **`main.py`** — Stateless scraper. Runs a single scrape-diff-notify cycle. Local mode uses APScheduler; GCP mode runs once and exits (Cloud Run Jobs triggered by Cloud Scheduler). Uses a temporary `discord.Client` that logs in, sends notifications, and closes.

- **`bot.py`** — Persistent Discord bot (`discord.ext.tasks`). Maintains an active connection, registers slash commands (`/stock`, `/stockimg`, `/update`, `/emoji-stats`, `/findprice`), and scrapes every 30 minutes. Requires the privileged `message_content` intent (used by `/emoji-stats`). Uses `WsBot(discord.Client)` with an `app_commands.CommandTree`.

### Data Flow

```
scrapers/hobbystation.py  ─┐
scrapers/fukufuku.py      ─┤─→ List[ProductSnapshot] → storage.compute_changes()
                            │                              → List[ChangeEvent]
                            └──────────────────────────→ storage.update_state() → state.json / GCS
                                                          discord_notifier.py → Discord channel
```

### Key Types (`scrapers/__init__.py`)

- `ProductSnapshot` — one scraped product. `state_key` property = `"{site}:{product_id}"` (e.g. `"hobbystation:247567"`).
- `ChangeEvent` — a detected change with `event_type` in `{"new", "price_change", "sold_out"}`.

### Storage (`storage.py`)

State is persisted as a flat JSON dict keyed by `state_key`. Two backends:
- **Local**: reads/writes `state.json`
- **GCS**: reads/writes a blob in a GCS bucket (used in GCP deployment)

`compute_changes()` returns no events on first run (empty state) unless `force_notify=True`.

### Notifications (`discord_notifier.py`)

Images are downloaded and attached as `discord.File` objects because shop servers block hotlinking. The `_download_image()` helper sends a browser-like `User-Agent` + `Referer` header. Two functions for sending:
- `send_notifications()` — used by `main.py`; creates a transient client
- `send_to_channel()` — used by `bot.py`; writes to an existing channel object

### Adding a New Scraper

1. Create `scrapers/yoursite.py` with a `scrape() -> List[ProductSnapshot]` function
2. Import and call it in both `main.py` (`run_once`) and `bot.py` (`_do_scrape_cycle`)
3. Add the site display name to `SITE_NAMES` in `bot.py` and `discord_notifier.py`

### Slash Commands

Commands in `bot.py` (`/stock`, `/stockimg`, `/update`) are registered directly on the `WsBot.tree`. Commands in `commands/` (`emoji_stats`, `find_price`) are added via `self.tree.add_command()` in `setup_hook`. All commands are synced to the guild (not globally) for instant availability.

### Card Lookup (`card_search/`, `commands/find_price.py`)

`/findprice card_1:"NIK/S135-001 UMA/W106-001"` looks up price and stock for up to 5 card numbers (`MAX_CARDS`) across shops, one embed per card, grouped by shop. Card numbers can be space-separated in `card_1` or entered in the optional `card_2`…`card_5` parameters (Tab moves between them in Discord). The last field of each embed is a divider plus the cheapest in-stock listing per card number, one line per rarity. NT$ prices (露天) are converted to ¥ with a live TWD→JPY rate (`card_search/fx.py`: open.er-api.com, then jsdelivr currency-api, cached 6 h, hard-coded fallback `FALLBACK_RATE`) so every shop competes in one ranking; NT$ picks show both prices and a rate footnote. Separate from the monitoring scrapers: each site module in `card_search/` exposes `search(card_no) -> List[CardListing]`.

- 遊々亭 (`yuyutei.py`), ふくふくとれか (`fukufuku.py`), ホビーステーション (`hobbystation.py`, reuses `_get()` for the bot-check cookie), まんぞく屋 (`manzokuya.py`), マスターズスクウェア (`mastersquare.py`, Bushiroad's official shop; lists sold-out items too; base cards have no `【…仕様】` title tag and carry their rarity as a suffix like `003R`/`001RR`, which the module strips so numbers line up with other shops), カードラボ (`clabo.py`, same platform as マスターズスクウェア; rarity is in `【】` and lower-grade copies like `※プレイ用特価品※` keep that note in the rarity label), グランドパンダキャニオン (`gurapan.py`, EC-CUBE; titles carry no card number, so it is read from the thumbnail path `…/WS/S135/small/S135-001SEC_small.jpg`; non-rarity tags like `【エラッタ前】` are kept in the rarity label), BIGWEB (`bigweb.py`, Angular SPA — uses the JSON API behind it, `api.bigweb.co.jp/products?game_id=126&name=…`; foil numbers are zero-padded (`001SEC`) but base cards are not (`1`), so base queries run a second unpadded search; sold-out items come back with price 0), 露天 牌塔 store (`ruten.py`, prices in NT$ via `CardListing.currency`; uses the JSON API behind the store's front-end — storeinfo → seller id → `core/seller/<id>/prod` → `items/v2/list` — which is undocumented and may change). ホビーステーション drops the series prefix (`WS-S135-001SP`), so the query is the part after `/`. まんぞく屋 uses lowercase underscore codes (`nik_s135_001sec`) and only lists in-stock items, so an empty result means out of stock (a module can set `EMPTY_TEXT` to customize the "not found" message).
- Matching (`card_search.matches`): a base number (`NIK/S135-001`) also returns every rarity (`…001SEC`, `…001SP`); a number with a suffix matches only itself.
- Card image comes from ws-tcg.com via a fixed path (`official_image_url`), falling back to the 遊々亭 image if it 404s.
- ドラゴンスター (dorasuta.jp) is behind Cloudflare, so only a search link is shown (currently a Google `site:` search).
- カードショップイセイ (cardshopisei.com, Shopify) answers `/search` and `/collections/…` (incl. `products.json`) with HTTP 429 `local_rate_limited` to scripts, so it is link-only too (`/search?q=<card>`). Its Vヴァイス deck collection could not be added to the monitoring scrapers for the same reason. Don't work around the rate limit.

## Deployment: GCP

Set `DEPLOYMENT_ENV=gcp` + `GCS_BUCKET` + `USE_SECRET_MANAGER=true`. State is stored in GCS; secrets come from Secret Manager. Cloud Scheduler triggers the Cloud Run Job every 30 minutes.
