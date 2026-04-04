"""
State management for WsToDiscord.
Supports both local JSON file and Google Cloud Storage backends.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from scrapers import ChangeEvent, ProductSnapshot

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# ── State dict type ──────────────────────────────────────────────────────────
# { "hobbystation:247567": { ...product fields..., "last_seen": "...", "first_seen": "..." } }
StateDict = Dict[str, Any]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_jst() -> str:
    return datetime.now(JST).isoformat()


def _snapshot_to_state_entry(snapshot: ProductSnapshot, first_seen: str) -> dict:
    return {
        "product_id": snapshot.product_id,
        "site": snapshot.site,
        "name": snapshot.name,
        "price_raw": snapshot.price_raw,
        "price_int": snapshot.price_int,
        "image_url": snapshot.image_url,
        "image_url_2": snapshot.image_url_2,
        "product_url": snapshot.product_url,
        "in_stock": snapshot.in_stock,
        "last_seen": _now_jst(),
        "first_seen": first_seen,
    }


# ── Local JSON backend ────────────────────────────────────────────────────────

def load_local(path: str) -> StateDict:
    if not os.path.exists(path):
        logger.info("storage: local state file not found, starting fresh: %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_local(path: str, state: StateDict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    logger.info("storage: saved local state to %s (%d entries)", path, len(state))


# ── GCS backend ──────────────────────────────────────────────────────────────

def load_gcs(bucket_name: str, blob_name: str) -> StateDict:
    from google.cloud import storage as gcs
    from google.cloud.exceptions import NotFound

    client = gcs.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    try:
        data = blob.download_as_text(encoding="utf-8")
        return json.loads(data)
    except NotFound:
        logger.info("storage: GCS blob not found, starting fresh: gs://%s/%s", bucket_name, blob_name)
        return {}


def save_gcs(bucket_name: str, blob_name: str, state: StateDict) -> None:
    from google.cloud import storage as gcs

    client = gcs.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(
        json.dumps(state, ensure_ascii=False, indent=2),
        content_type="application/json",
    )
    logger.info("storage: saved GCS state to gs://%s/%s (%d entries)", bucket_name, blob_name, len(state))


# ── Change detection ─────────────────────────────────────────────────────────

def compute_changes(
    scraped: List[ProductSnapshot],
    stored: StateDict,
    is_first_run: bool,
) -> List[ChangeEvent]:
    """
    Compare scraped products against stored state and return change events.

    On first run (empty state), returns no events — just populates state.
    """
    events: List[ChangeEvent] = []

    if is_first_run:
        logger.info("storage: first run detected, skipping notifications")
        return events

    scraped_keys = {s.state_key for s in scraped}

    # Check each scraped product against stored state
    for snapshot in scraped:
        key = snapshot.state_key
        if key not in stored:
            # New product
            logger.info("storage: new product: %s - %s", key, snapshot.name)
            events.append(ChangeEvent(event_type="new", snapshot=snapshot))
        else:
            stored_entry = stored[key]
            old_price_int = stored_entry.get("price_int", 0)
            old_price_raw = stored_entry.get("price_raw", "")
            was_in_stock = stored_entry.get("in_stock", True)

            # Price change
            if snapshot.price_int != 0 and old_price_int != 0 and snapshot.price_int != old_price_int:
                logger.info(
                    "storage: price change: %s %s -> %s",
                    key, old_price_raw, snapshot.price_raw
                )
                events.append(ChangeEvent(
                    event_type="price_change",
                    snapshot=snapshot,
                    old_price_int=old_price_int,
                    old_price_raw=old_price_raw,
                ))

            # Sold out (was in stock, now not)
            if was_in_stock and not snapshot.in_stock:
                logger.info("storage: sold out: %s - %s", key, snapshot.name)
                events.append(ChangeEvent(event_type="sold_out", snapshot=snapshot))

    # Check for products that disappeared from scrape
    for key in list(stored.keys()):
        if key not in scraped_keys:
            stored_entry = stored[key]
            if stored_entry.get("in_stock", True):
                # Was in stock but disappeared — treat as sold out
                logger.info("storage: product disappeared while in stock: %s", key)
                snap = ProductSnapshot(
                    site=stored_entry["site"],
                    product_id=stored_entry["product_id"],
                    name=stored_entry["name"],
                    price_int=stored_entry.get("price_int", 0),
                    price_raw=stored_entry.get("price_raw", ""),
                    image_url=stored_entry.get("image_url", ""),
                    image_url_2=stored_entry.get("image_url_2", ""),
                    product_url=stored_entry.get("product_url", ""),
                    in_stock=False,
                )
                events.append(ChangeEvent(event_type="sold_out", snapshot=snap))

    return events


def update_state(scraped: List[ProductSnapshot], stored: StateDict) -> StateDict:
    """
    Return a new state dict updated with current scraped products.
    Products that disappeared from scrape are removed from state.
    """
    now = _now_jst()
    new_state: StateDict = {}
    scraped_keys = {s.state_key for s in scraped}

    for snapshot in scraped:
        key = snapshot.state_key
        # Preserve first_seen if we've seen this product before
        first_seen = stored.get(key, {}).get("first_seen", now)
        new_state[key] = _snapshot_to_state_entry(snapshot, first_seen)

    # Log removed products
    for key in stored:
        if key not in scraped_keys:
            logger.info("storage: removing delisted product from state: %s", key)

    return new_state


# ── Site-scoped helpers (for independent per-site scrape cycles) ─────────────

def compute_changes_for_site(
    scraped: List[ProductSnapshot],
    stored: StateDict,
    site: str,
    is_first_run: bool,
) -> List[ChangeEvent]:
    """
    Like compute_changes(), but only considers stored entries belonging to *site*.

    Use this when a site is scraped on its own schedule so that products from
    other sites are not mistakenly flagged as disappeared.
    """
    site_prefix = f"{site}:"
    site_stored = {k: v for k, v in stored.items() if k.startswith(site_prefix)}
    return compute_changes(scraped, site_stored, is_first_run)


def update_state_for_site(
    scraped: List[ProductSnapshot],
    stored: StateDict,
    site: str,
) -> StateDict:
    """
    Like update_state(), but only replaces entries belonging to *site*.

    Entries for other sites in *stored* are preserved unchanged.
    """
    site_prefix = f"{site}:"
    # Keep all entries that don't belong to this site
    other_sites = {k: v for k, v in stored.items() if not k.startswith(site_prefix)}
    # Build updated entries for this site
    site_new = update_state(scraped, {k: v for k, v in stored.items() if k.startswith(site_prefix)})
    return {**other_sites, **site_new}
