"""Card lookup on square-bushiroad.com (マスターズスクウェア通販ブシロード店)."""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from card_search import CardListing, matches
from scrapers.hobbystation import HEADERS

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.square-bushiroad.com/product-list"
SITE = "mastersquare"

# "[WS_OSK/S133-028_SSP]", "[WS_OSK/S133-028RR]", "[WS_ OSK/S133-028 RR]"
_CODE_RE = re.compile(
    r"\[WS_\s*(?P<base>[A-Z0-9]+/[A-Z0-9]+-[A-Z]*\d+)[_\s]*(?P<suffix>[A-Z]*)\s*\]\s*$"
)
# Parallel (foil) versions: "S" is the SR suffix, "R" is the RRR suffix in the WS numbering scheme
_PARALLEL_RARITY_ALIASES = {"S": "SR", "R": "RRR"}


def search(card_no: str) -> List[CardListing]:
    # The shop writes some codes as "001_SEC", so a keyword with the suffix misses them;
    # search by the base number and let matches() pick the requested rarity.
    keyword = re.sub(r"[A-Z]+$", "", card_no)
    resp = requests.get(SEARCH_URL, params={"keyword": keyword}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    results: List[CardListing] = []
    for li in soup.select("li.list_item_cell"):
        name_tag = li.select_one(".goods_name")
        link = li.select_one("a.item_data_link")
        if not name_tag or not link:
            continue
        full_name = name_tag.get_text("", strip=True)
        m = _CODE_RE.search(full_name)
        if not m:
            continue
        suffix = m.group("suffix")
        # Titles of parallel versions start with a 【…仕様】 tag. Base cards have none, and
        # the letters after their number (003R, 001RR, 01N, T01TD) are just the rarity,
        # whereas every other shop writes the base number without a suffix.
        is_parallel = full_name.startswith("【")
        if is_parallel:
            found_no = m.group("base") + suffix
            rarity = _PARALLEL_RARITY_ALIASES.get(suffix, suffix)
        else:
            found_no = m.group("base")
            rarity = suffix
        if not matches(card_no, found_no):
            continue

        # "【SR星3】Happy White Day アクア[WS_…]" -> "Happy White Day アクア"
        name = re.sub(r"^【[^】]*】", "", full_name[: m.start()]).strip()

        price_tag = li.select_one(".selling_price .figure")
        price_match = re.search(r"([\d,]+)\s*円", price_tag.get_text() if price_tag else "")
        price_raw = price_match.group(1) if price_match else ""

        stock_tag = li.select_one(".stock")
        stock_str = stock_tag.get_text(" ", strip=True) if stock_tag else ""
        in_stock = bool(stock_str) and "在庫なし" not in stock_str
        stock_num = re.search(r"(\d+)\s*点", stock_str)
        img = li.select_one("img")

        results.append(CardListing(
            site=SITE,
            card_no=found_no,
            rarity=rarity,
            name=name,
            price_int=int(price_raw.replace(",", "")) if price_raw else 0,
            price_raw=price_raw,
            stock_text=stock_num.group(1) if stock_num else ("有貨" if in_stock else "品切れ"),
            in_stock=in_stock,
            url=link["href"],
            image_url=(img.get("data-x2") or img.get("src", "")) if img else "",
        ))
    return results
