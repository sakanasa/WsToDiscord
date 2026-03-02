# Local Setup Guide

Run WsToDiscord locally with APScheduler (every 30 minutes).

## Prerequisites

- Python 3.12+
- A Discord bot token and channel ID

## Setup

### 1. Create virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `DISCORD_BOT_TOKEN` — your bot token from the [Discord Developer Portal](https://discord.com/developers/applications)
- `DISCORD_CHANNEL_ID` — the channel where notifications should be sent
- Leave `DEPLOYMENT_ENV=local`

### 4. Invite the bot to your server

In the Discord Developer Portal, under **OAuth2 → URL Generator**:
- Scopes: `bot`
- Bot Permissions: `Send Messages`, `Embed Links`

Copy the generated URL, open it in your browser, and add the bot to your server.

### 5. Run

```bash
python main.py
```

The bot will:
1. Scrape both shops immediately on startup
2. On the **first run** (empty `state.json`), save state without sending any notifications
3. Every 30 minutes thereafter, detect changes and send Discord notifications

## Testing

### Test scrapers

```bash
# Hobbystation
python -c "
from scrapers.hobbystation import scrape
products = scrape()
print(f'Found {len(products)} products')
for p in products[:3]:
    print(f'  {p.product_id}: {p.name} ¥{p.price_raw} in_stock={p.in_stock}')
"

# Fukufuku
python -c "
from scrapers.fukufuku import scrape
products = scrape()
print(f'Found {len(products)} products')
for p in products[:3]:
    print(f'  {p.product_id}: {p.name} ¥{p.price_raw} in_stock={p.in_stock}')
"
```

### Test Discord notifications (force send)

Delete `state.json` if it exists, then run with `--force-notify`:

```bash
rm -f state.json
python main.py --force-notify
```

This will treat all scraped products as "new" and send Discord notifications.

### Test price change detection

1. Run once to populate `state.json`
2. Edit `state.json` — change a `price_int` / `price_raw` value for one product
3. Run again: you should receive a price change notification

## Local Docker test

```bash
docker build -t ws-discord .
docker run --env-file .env -e DEPLOYMENT_ENV=local ws-discord
```

## GCP Deployment overview

See the plan document for full GCP setup. Key steps:

1. **Build & push image**
   ```bash
   gcloud auth configure-docker REGION-docker.pkg.dev
   docker build -t REGION-docker.pkg.dev/PROJECT/REPO/ws-discord:latest .
   docker push REGION-docker.pkg.dev/PROJECT/REPO/ws-discord:latest
   ```

2. **Create Cloud Run Job**
   ```bash
   gcloud run jobs create ws-discord-job \
     --image REGION-docker.pkg.dev/PROJECT/REPO/ws-discord:latest \
     --region REGION \
     --set-env-vars DEPLOYMENT_ENV=gcp,GCS_BUCKET=BUCKET,USE_SECRET_MANAGER=true
   ```

3. **Create Cloud Scheduler**
   ```bash
   gcloud scheduler jobs create http ws-discord-scheduler \
     --schedule "*/30 * * * *" \
     --time-zone "Asia/Tokyo" \
     --uri "https://REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/PROJECT/jobs/ws-discord-job:run" \
     --http-method POST \
     --oauth-service-account-email SERVICE_ACCOUNT
   ```

4. **Manual test run**
   ```bash
   gcloud run jobs execute ws-discord-job --wait
   ```
