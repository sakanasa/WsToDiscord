"""
WsToDiscord - Main entry point.

Monitors two Japanese WS card shops for new listings, price changes, and
sold-out events, then posts Discord notifications.

Deployment modes:
  - GCP (Cloud Run Jobs): set DEPLOYMENT_ENV=gcp  → runs once and exits
  - Local (APScheduler):  set DEPLOYMENT_ENV=local → runs every 30 minutes
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

DEPLOYMENT_ENV = os.environ.get("DEPLOYMENT_ENV", "local").lower()

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))

# Storage: local JSON
LOCAL_STATE_PATH = os.environ.get("LOCAL_STATE_PATH", "state.json")

# Storage: GCS
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
GCS_BLOB = os.environ.get("GCS_BLOB", "state.json")

# Secret Manager (optional override for GCP secrets)
USE_SECRET_MANAGER = os.environ.get("USE_SECRET_MANAGER", "false").lower() == "true"
SECRET_DISCORD_TOKEN = os.environ.get("SECRET_DISCORD_TOKEN", "ws-discord-bot-token")
SECRET_CHANNEL_ID = os.environ.get("SECRET_CHANNEL_ID", "ws-discord-channel-id")


def _load_gcp_secrets() -> tuple[str, int]:
    """Load Discord credentials from GCP Secret Manager."""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or _get_project_id()

    def _access(secret_id: str) -> str:
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()

    token = _access(SECRET_DISCORD_TOKEN)
    channel_id = int(_access(SECRET_CHANNEL_ID))
    return token, channel_id


def _get_project_id() -> str:
    import urllib.request
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode()
    except Exception:
        raise RuntimeError("Could not determine GCP project ID. Set GOOGLE_CLOUD_PROJECT env var.")


def _get_credentials() -> tuple[str, int]:
    """Resolve Discord bot token and channel ID from env or Secret Manager."""
    if USE_SECRET_MANAGER:
        return _load_gcp_secrets()

    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN is not set")
        sys.exit(1)
    if DISCORD_CHANNEL_ID == 0:
        logger.error("DISCORD_CHANNEL_ID is not set")
        sys.exit(1)

    return DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID


# ── Core run logic ────────────────────────────────────────────────────────────

def run_once(force_notify: bool = False) -> None:
    """
    Execute one scrape-diff-notify cycle.

    Args:
        force_notify: If True, send notifications even on first run.
                      Useful for testing Discord embed formatting.
    """
    import scrapers.hobbystation as hobbystation_scraper
    import scrapers.fukufuku as fukufuku_scraper
    import storage
    import discord_notifier

    bot_token, channel_id = _get_credentials()

    # 1. Load state
    if DEPLOYMENT_ENV == "gcp":
        if not GCS_BUCKET:
            logger.error("GCS_BUCKET is not set for GCP deployment")
            sys.exit(1)
        stored = storage.load_gcs(GCS_BUCKET, GCS_BLOB)
    else:
        stored = storage.load_local(LOCAL_STATE_PATH)

    is_first_run = len(stored) == 0 and not force_notify

    # 2. Scrape both sites
    logger.info("Scraping hobbystation...")
    hobbystation_products = hobbystation_scraper.scrape()
    logger.info("hobbystation: %d products", len(hobbystation_products))

    logger.info("Scraping fukufuku...")
    fukufuku_products = fukufuku_scraper.scrape()
    logger.info("fukufuku: %d products", len(fukufuku_products))

    all_products = hobbystation_products + fukufuku_products
    logger.info("Total products scraped: %d", len(all_products))

    # 3. Compute changes
    events = storage.compute_changes(all_products, stored, is_first_run)
    logger.info("Change events: %d", len(events))

    # 4. Update and save state BEFORE sending notifications
    new_state = storage.update_state(all_products, stored)

    if DEPLOYMENT_ENV == "gcp":
        storage.save_gcs(GCS_BUCKET, GCS_BLOB, new_state)
    else:
        storage.save_local(LOCAL_STATE_PATH, new_state)

    # 5. Send Discord notifications
    if events:
        logger.info("Sending %d notification(s) to Discord...", len(events))
        asyncio.run(discord_notifier.send_notifications(events, bot_token, channel_id))
    else:
        logger.info("No changes detected, nothing to notify")

    if is_first_run:
        logger.info("First run complete. State initialized with %d products.", len(new_state))


# ── Entry points ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="WsToDiscord - WS card shop monitor")
    parser.add_argument(
        "--force-notify",
        action="store_true",
        help="Send notifications even on first run (useful for testing)",
    )
    args = parser.parse_args()

    if DEPLOYMENT_ENV == "gcp":
        logger.info("Running in GCP mode (Cloud Run Jobs)")
        run_once(force_notify=args.force_notify)
    else:
        logger.info("Running in local mode (APScheduler)")
        _run_local(force_notify=args.force_notify)


def _run_local(force_notify: bool = False) -> None:
    """Run with APScheduler, executing immediately and then every 30 minutes."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    # Validate credentials before starting the scheduler
    _get_credentials()

    scheduler = BlockingScheduler(timezone="Asia/Tokyo")

    # Run immediately on startup, then every 30 minutes
    now = datetime.now(timezone(timedelta(hours=9)))
    scheduler.add_job(
        lambda: run_once(force_notify=force_notify),
        trigger=IntervalTrigger(minutes=30),
        next_run_time=now,
        id="ws_monitor",
        max_instances=1,
        coalesce=True,
    )

    logger.info("Scheduler started. Running every 30 minutes (JST). Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")


if __name__ == "__main__":
    main()
