"""
Scraper for https://weis.fukufukutoreka.com/products/list?category_id=2
Price is embedded in JavaScript: eccube.productsClassCategories
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from scrapers import ProductSnapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://weis.fukufukutoreka.com"
LIST_URL = "https://weis.fukufukutoreka.com/products/list?category_id=2"
PAGE_SIZE = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def _extract_prices_from_scripts(soup: BeautifulSoup) -> dict[str, tuple[int, str]]:
    """
    Extract product prices from embedded JavaScript.

    Actual structure:
    eccube.productsClassCategories = {
      "80002": {
        "__unselected2": {
          "#": {
            "price02_inc_tax": "40,000",
            ...
          }
        }
      }
    }
    """
    prices: dict[str, tuple[int, str]] = {}

    for script in soup.find_all("script"):
        script_text = script.string
        if not script_text or "productsClassCategories" not in script_text:
            continue

        try:
            match = re.search(
                r'eccube\.productsClassCategories\s*=\s*(\{.*?\});',
                script_text,
                re.DOTALL,
            )
            if not match:
                match = re.search(
                    r'eccube\.productsClassCategories\s*=\s*(\{.*\})',
                    script_text,
                    re.DOTALL,
                )
            if not match:
                logger.warning("fukufuku: found productsClassCategories but could not extract JSON")
                continue

            data = json.loads(match.group(1))

            # Structure: product_id -> outer_key -> inner_key -> price fields
            for product_id, outer in data.items():
                if not isinstance(outer, dict):
                    continue
                # Iterate through all nested levels to find price fields
                for outer_val in outer.values():
                    if not isinstance(outer_val, dict):
                        continue
                    for inner_val in outer_val.values():
                        if not isinstance(inner_val, dict):
                            continue
                        price_str = (
                            inner_val.get("price02_inc_tax")
                            or inner_val.get("price01_inc_tax")
                            or inner_val.get("price02")
                            or ""
                        )
                        if price_str:
                            try:
                                price_int = int(str(price_str).replace(",", "").strip())
                                price_raw = f"{price_int:,}"
                                prices[str(product_id)] = (price_int, price_raw)
                            except (ValueError, TypeError):
                                pass
                            break
                    if str(product_id) in prices:
                        break

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("fukufuku: failed to parse productsClassCategories JSON: %s", e)

    return prices


def scrape() -> List[ProductSnapshot]:
    """Fetch and parse all products from weis.fukufukutoreka.com"""
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("fukufuku: request failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    products: List[ProductSnapshot] = []

    prices = _extract_prices_from_scripts(soup)
    logger.info("fukufuku: extracted prices for %d products from JS", len(prices))

    items = soup.find_all("li", class_="product-list__item")
    logger.info("fukufuku: found %d items", len(items))

    if len(items) > 0 and len(items) % PAGE_SIZE == 0:
        logger.warning(
            "fukufuku: item count %d is a multiple of page size %d — "
            "there may be additional pages not being scraped",
            len(items), PAGE_SIZE,
        )

    for item in items:
        try:
            product = _parse_item(item, prices)
            if product:
                products.append(product)
        except Exception as e:
            logger.error("fukufuku: error parsing item: %s", e)

    return products


def _parse_item(item, prices: dict[str, tuple[int, str]]) -> Optional[ProductSnapshot]:
    """Parse a single product list item."""
    # Title class is product-list__item__title--name
    title_tag = (
        item.find("h2", class_="product-list__item__title--name")
        or item.find("h2")
    )
    if not title_tag:
        return None

    link_tag = title_tag.find("a", href=re.compile(r"/products/detail/\d+"))
    if not link_tag:
        return None

    href = link_tag.get("href", "")
    id_match = re.search(r"/products/detail/(\d+)", href)
    if not id_match:
        return None

    product_id = id_match.group(1)
    product_url = href if href.startswith("http") else BASE_URL + href
    name = link_tag.get_text(strip=True) or f"商品 {product_id}"

    price_int, price_raw = prices.get(product_id, (0, ""))
    if price_int == 0:
        logger.warning("fukufuku: no price found for product %s (%s)", product_id, name)

    # Image: lazy-load uses data-src
    image_url = ""
    img_tag = item.find("img")
    if img_tag:
        src = img_tag.get("data-src") or img_tag.get("src", "")
        if src:
            image_url = src if src.startswith("http") else BASE_URL + src

    # Stock: 品切れ中 in text or disabled button
    item_text = item.get_text()
    in_stock = "品切れ" not in item_text

    return ProductSnapshot(
        site="fukufuku",
        product_id=product_id,
        name=name,
        price_int=price_int,
        price_raw=price_raw,
        image_url=image_url,
        product_url=product_url,
        in_stock=in_stock,
    )
