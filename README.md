# WsToDiscord

A Discord bot that monitors two Japanese Weiss Schwarz card shops every 30 minutes and sends notifications for new listings, price changes, and sold-out events.

## Target Sites

| Site | Products |
|------|----------|
| [ホビーステーション](https://hobbystation-single.jp) | ~19 products |
| [ふくふくとれか](https://weis.fukufukutoreka.com) | ~6 products |

## Features

- Automatic scrape every 30 minutes
- Discord notifications for:
  - New listings
  - Price changes
  - Sold-out events
- Slash commands:
  - `/stock` — Single embed showing all in-stock products grouped by shop
  - `/stockimg` — Per-product embeds with product images
  - `/update` — Force an immediate scrape and notify cycle

## Setup

### Prerequisites

- Python 3.12+
- Discord bot token and channel ID

### 1. Clone and install

```bash
git clone https://github.com/sakanasa/WsToDiscord.git
cd WsToDiscord
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_CHANNEL_ID=your_channel_id
DISCORD_GUILD_ID=your_guild_id
DEPLOYMENT_ENV=local
```

### 3. Invite the bot

In the [Discord Developer Portal](https://discord.com/developers/applications), go to **OAuth2 → URL Generator**:
- Scopes: `bot`, `applications.commands`
- Bot Permissions: `Send Messages`, `Embed Links`

### 4. Run

```bash
python bot.py
```

On first run, the bot saves state without sending notifications to avoid a flood. Subsequent runs detect and notify changes.

## Testing

### Test scrapers

```bash
python -c "from scrapers.hobbystation import scrape; [print(p) for p in scrape()[:3]]"
python -c "from scrapers.fukufuku import scrape; [print(p) for p in scrape()[:3]]"
```

### Force notification (for testing)

```bash
rm -f state.json
python main.py --force-notify
```

## Docker

```bash
docker build -t ws-discord .
docker run --env-file .env -e DEPLOYMENT_ENV=local ws-discord
```

## Deployment (GCP Cloud Run Jobs)

Set `DEPLOYMENT_ENV=gcp` to run once instead of looping. Use Cloud Scheduler to trigger every 30 minutes and GCS for state storage.

See [`LOCAL_SETUP.md`](LOCAL_SETUP.md) for full GCP setup instructions.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_BOT_TOKEN` | Bot token from Developer Portal | required |
| `DISCORD_CHANNEL_ID` | Channel to send notifications | required |
| `DISCORD_GUILD_ID` | Guild ID for slash command sync | required |
| `DEPLOYMENT_ENV` | `local` or `gcp` | `local` |
| `LOCAL_STATE_PATH` | Path to state file | `state.json` |
| `GCS_BUCKET` | GCS bucket name (GCP mode) | — |
| `GCS_BLOB` | GCS blob name (GCP mode) | — |
| `USE_SECRET_MANAGER` | Load Discord creds from Secret Manager | `false` |
