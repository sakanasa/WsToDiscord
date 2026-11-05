"""Card lookup on shopmanzokuya.com (まんぞく屋).

The shop only lists items that are in stock, so "no result" means out of stock
or not carried. Product codes are lowercase with underscores (nik_s135_001sec).
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from card_search import CardListing, matches
from scrapers.hobbystation import HEADERS

logger = logging.getLogger(__name__)

BASE_URL = "https://shopmanzokuya.com"
SEARCH_URL = f"{BASE_URL}/products/list"
WS_CATEGORY_ID = 1003
SITE = "manzokuya"
EMPTY_TEXT = "無庫存（此店只列出有貨商品）"


def _to_shop_code(card_no: str) -> str:
    """NIK/S135-001 -> nik_s135_001"""
    series, rest = card_no.split("/", 1)
    return f"{series}_{rest.replace('-', '_')}".lower()


def _from_shop_code(code: str) -> str | None:
    """nik_s135_001sec -> NIK/S135-001SEC"""
    parts = code.strip().split("_")
    if len(parts) != 3:
        return None
    return f"{parts[0]}/{parts[1]}-{parts[2]}".upper()


def search(card_no: str) -> List[CardListing]:
    resp = requests.get(
        SEARCH_URL,
        params={"name": _to_shop_code(card_no), "category_id": WS_CATEGORY_ID},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    results: List[CardListing] = []
    for li in soup.select("li.ec-shelfGrid__item"):
        code_tag = li.select_one(".mzk-m")
        found_no = _from_shop_code(code_tag.get_text(strip=True)) if code_tag else None
        if not found_no or not matches(card_no, found_no):
            continue

        link = li.select_one("a[href]")
        rarity_tag = li.select_one(".mzk-r")
        name_tag = li.select_one(".mzk-n")
        price_tag = li.select_one(".price02-default")
        stock_tag = li.select_one(".productStock span")
        img = li.select_one("img")

        price_match = re.search(r"[￥¥]\s*([\d,]+)", price_tag.get_text(" ") if price_tag else "")
        price_raw = price_match.group(1) if price_match else ""
        in_stock = li.select_one("button.add-cart") is not None
        stock_text = stock_tag.get_text(strip=True) if stock_tag else "有貨"
        img_src = img.get("src", "") if img else ""

        results.append(CardListing(
            site=SITE,
            card_no=found_no,
            rarity=rarity_tag.get_text(strip=True) if rarity_tag else "",
            name=name_tag.get_text(strip=True) if name_tag else "",
            price_int=int(price_raw.replace(",", "")) if price_raw else 0,
            price_raw=price_raw,
            stock_text=stock_text,
            in_stock=in_stock,
            url=link["href"] if link else SEARCH_URL,
            image_url=(BASE_URL + img_src) if img_src.startswith("/") else img_src,
        ))
    return results
