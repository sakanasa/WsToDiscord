"""
Scraper for https://www.hobbystation-single.jp/ws/product/list
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers import ProductSnapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://www.hobbystation-single.jp"
LIST_URL = (
    "https://www.hobbystation-single.jp/ws/product/list"
    "?HbstSearchOptions[0][id]=16"
    "&HbstSearchOptions[0][search_keyword]=(BANNER)%E3%82%AA%E3%83%AA%E3%82%B8%E3%83%8A%E3%83%AB%E3%83%87%E3%83%83%E3%82%AD(BANNER)"
    "&HbstSearchOptions[0][Type]=2"
)
PAGE_SIZE = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def _parse_price(text: str) -> tuple[int, str]:
    """Parse price text like '1,500円' -> (1500, '1,500')"""
    match = re.search(r'([\d,]+)円', text.replace('\u00a5', '').replace('¥', ''))
    if match:
        price_raw = match.group(1)
        price_int = int(price_raw.replace(',', ''))
        return price_int, price_raw
    return 0, ""


def _fetch_detail_images(product_url: str) -> tuple[str, str]:
    """Fetch main and thumbnail image URLs from product detail page.

    Returns (main_url, thumb_url). Either may be empty on failure.
    Paths are returned as absolute URLs without extra encoding
    (discord_notifier._encode_url handles encoding uniformly).
    """
    try:
        resp = requests.get(product_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("hobbystation: detail page request failed for %s: %s", product_url, e)
        return "", ""

    soup = BeautifulSoup(resp.text, "lxml")
    main_img = ""
    thumb_img = ""

    # Product images live inside the .thumb-item carousel;
    # searching only there avoids picking up related-product images.
    container = soup.select_one(".thumb-item")
    search_root = container if container else soup

    for img in search_root.find_all("img"):
        src = img.get("src", "")
        if "/upload/save_image/" not in src:
            continue
        abs_src = src if src.startswith("http") else BASE_URL + src
        if "_サムネ" in src:
            thumb_img = abs_src
        else:
            main_img = abs_src

    return main_img, thumb_img


def scrape() -> List[ProductSnapshot]:
    """Fetch and parse all products from hobbystation-single.jp"""
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("hobbystation: request failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    products: List[ProductSnapshot] = []

    # Find all <li> elements that contain a product detail link
    items = [
        li for li in soup.find_all("li")
        if li.find("a", href=re.compile(r"/ws/product/detail/\d+"))
    ]
    logger.info("hobbystation: found %d items", len(items))

    if len(items) > 0 and len(items) % PAGE_SIZE == 0:
        logger.warning(
            "hobbystation: item count %d is a multiple of page size %d — "
            "there may be additional pages not being scraped",
            len(items), PAGE_SIZE,
        )

    for item in items:
        try:
            product = _parse_item(item)
            if product:
                products.append(product)
        except Exception as e:
            logger.error("hobbystation: error parsing item: %s", e)

    return products


def _parse_item(item) -> ProductSnapshot | None:
    """Parse a single list item."""
    # Product link and ID
    link_tag = item.find("a", href=re.compile(r"/ws/product/detail/\d+"))
    if not link_tag:
        return None

    href = link_tag.get("href", "")
    id_match = re.search(r"/ws/product/detail/(\d+)", href)
    if not id_match:
        return None

    product_id = id_match.group(1)
    product_url = href if href.startswith("http") else BASE_URL + href

    # Product name: find the link whose text is non-empty
    name = ""
    for a in item.find_all("a", href=re.compile(r"/ws/product/detail/\d+")):
        text = a.get_text(strip=True)
        if text:
            name = text
            break
    if not name:
        name = f"商品 {product_id}"

    # Price: direct text inside div.packageDetail (e.g. "1,500円")
    price_int, price_raw = 0, ""
    package_div = item.find("div", class_="packageDetail")
    if package_div:
        for text_node in package_div.strings:
            text = text_node.strip()
            if "円" in text:
                price_int, price_raw = _parse_price(text)
                if price_int:
                    break

    # Stock: SOLD OUT img has alt="SOLD OUT"
    in_stock = True
    soldout_img = item.find("img", alt=re.compile(r"SOLD\s*OUT", re.IGNORECASE))
    if soldout_img:
        in_stock = False

    # Images: fetch from detail page
    main_img, thumb_img = _fetch_detail_images(product_url)

    return ProductSnapshot(
        site="hobbystation",
        product_id=product_id,
        name=name,
        price_int=price_int,
        price_raw=price_raw,
        image_url=main_img,
        image_url_2=thumb_img,
        product_url=product_url,
        in_stock=in_stock,
    )
