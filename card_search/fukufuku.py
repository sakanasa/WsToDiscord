"""Card lookup on weis.fukufukutoreka.com (ふくふくとれか)."""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from card_search import CardListing, matches
from scrapers.hobbystation import HEADERS

logger = logging.getLogger(__name__)

BASE_URL = "https://weis.fukufukutoreka.com"
SEARCH_URL = f"{BASE_URL}/products/list"
SITE = "fukufuku"

# Title: "エレグ：ブーム・アンド・ショック(SEC)（NIK/S135-001SEC)"
_TITLE_RE = re.compile(r"^(?P<name>.*?)[(（](?P<rarity>[^()（）]*)[)）][（(](?P<no>[^）)]+)[）)]\s*$")


def search(card_no: str) -> List[CardListing]:
    resp = requests.get(SEARCH_URL, params={"name": card_no}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    results: List[CardListing] = []
    for li in soup.select("li.product-list__item"):
        title_tag = li.select_one("a.product-list__item__title--name")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        m = _TITLE_RE.match(title)
        if not m or not matches(card_no, m.group("no")):
            continue

        href = title_tag.get("href", "")
        url = href if href.startswith("http") else BASE_URL + href

        sold_out = li.select_one(".product-list__item__message") is not None
        price_raw, price_int, stock_text = "", 0, "品切れ"
        in_stock = False
        if not sold_out:
            cart = li.select_one(".js-product-list-cart")
            price_match = re.search(r"[￥¥]\s*([\d,]+)", cart.get_text(" ") if cart else "")
            if price_match:
                price_raw = price_match.group(1)
                price_int = int(price_raw.replace(",", ""))
            # Stock row reads "1 /1" -> remaining stock is the number after the slash
            row = li.select_one(".productList--item--stock--row")
            stock_match = re.search(r"/\s*(\d+)", row.get_text(" ") if row else "")
            stock_text = stock_match.group(1) if stock_match else "有貨"
            in_stock = True

        img = li.select_one("img.lazy")
        img_src = img.get("data-src") or img.get("src", "") if img else ""
        results.append(CardListing(
            site=SITE,
            card_no=m.group("no"),
            rarity=m.group("rarity"),
            name=m.group("name"),
            price_int=price_int,
            price_raw=price_raw,
            stock_text=stock_text,
            in_stock=in_stock,
            url=url,
            image_url=(BASE_URL + img_src) if img_src.startswith("/") else img_src,
        ))
    return results
