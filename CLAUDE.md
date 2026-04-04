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

- **`bot.py`** — Persistent Discord bot (`discord.ext.tasks`). Maintains an active connection, registers slash commands (`/stock`, `/stockimg`, `/update`, `/emoji-stats`), and scrapes every 30 minutes. Uses `WsBot(discord.Client)` with an `app_commands.CommandTree`.

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

Commands in `bot.py` are registered directly on the `WsBot.tree`. Commands in `commands/` (currently only `emoji_stats`) are added via `self.tree.add_command()` in `setup_hook`. All commands are synced to the guild (not globally) for instant availability.

## Deployment: GCP

Set `DEPLOYMENT_ENV=gcp` + `GCS_BUCKET` + `USE_SECRET_MANAGER=true`. State is stored in GCS; secrets come from Secret Manager. Cloud Scheduler triggers the Cloud Run Job every 30 minutes.
