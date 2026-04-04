"""
Scraper for tw.mercari.com

Searches for Weiss Schwarz decks filtered to in-stock items, sorted by newest,
minimum price NT$300. Parses product data from the RSC (React Server Components)
payload embedded in the HTML response — no browser rendering required.
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests

from scrapers import ProductSnapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://tw.mercari.com"
SEARCH_URL = (
    "https://tw.mercari.com/zh-hant/search"
    "?keyword=%E3%83%B4%E3%82%A1%E3%82%A4%E3%82%B9%E3%82%B7%E3%83%A5%E3%83%B4%E3%82%A1%E3%83%AB%E3%83%84"
    "+%E3%83%87%E3%83%83%E3%82%AD+fate"
    "&status=in-stock"
    "&availability=1"
    "&sort=1"
    "&price-min=300"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Matches escaped JSON item objects embedded in the RSC payload.
# Each item has: id (UUID), thumbnailUrl, title, formattedAmount (price digits).
_ITEM_RE = re.compile(
    r'\\"id\\":\\"([a-f0-9-]{36})\\"'
    r'.*?\\"thumbnailUrl\\":\\"(https://[^\\]+)\\"'
    r'.*?\\"title\\":\\"([^\\"]+)\\"'
    r'.*?\\"formattedAmount\\":\\"(\d+)\\"',
    re.DOTALL,
)


def scrape() -> List[ProductSnapshot]:
    """Fetch and parse all in-stock products from the Mercari TW search URL."""
    try:
        resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("mercari: request failed: %s", e)
        return []

    items = _ITEM_RE.findall(resp.text)
    logger.info("mercari: found %d items", len(items))

    products: List[ProductSnapshot] = []
    seen_ids: set[str] = set()

    for uuid, thumb_url, title, price_str in items:
        # The RSC payload may repeat items; deduplicate by UUID
        if uuid in seen_ids:
            continue
        seen_ids.add(uuid)

        try:
            price_int = int(price_str)
            price_raw = f"{price_int:,}"
            product_url = f"{BASE_URL}/zh-hant/items/{uuid}"

            products.append(ProductSnapshot(
                site="mercari",
                product_id=uuid,
                name=title,
                price_int=price_int,
                price_raw=price_raw,
                image_url=thumb_url,
                image_url_2="",
                product_url=product_url,
                in_stock=True,  # URL already filters for in-stock only
            ))
        except Exception as e:
            logger.error("mercari: error parsing item %s: %s", uuid, e)

    return products
