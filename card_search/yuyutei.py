"""Card lookup on yuyu-tei.jp (遊々亭)."""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from card_search import CardListing, matches, rarity_of
from scrapers.hobbystation import HEADERS

logger = logging.getLogger(__name__)

SEARCH_URL = "https://yuyu-tei.jp/sell/ws/s/search"
SITE = "yuyutei"


def search(card_no: str) -> List[CardListing]:
    resp = requests.get(SEARCH_URL, params={"search_word": card_no}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    results: List[CardListing] = []
    for card in soup.select("div.card-product"):
        no_tag = card.select_one("span.border")
        if not no_tag:
            continue
        found_no = no_tag.get_text(strip=True)
        if not matches(card_no, found_no):
            continue

        link = card.select_one("a[href]")
        img = card.select_one("img.card")
        name_tag = card.select_one("h4")
        price_tag = card.select_one("strong")
        stock_tag = card.select_one(".cart_sell_zaiko")

        # img alt looks like "NIK/S135-001SEC SEC エレグ：…" -> second token is the rarity
        alt_parts = (img.get("alt", "") if img else "").split()
        rarity = alt_parts[1] if len(alt_parts) > 1 else rarity_of(card_no, found_no)

        price_match = re.search(r"([\d,]+)\s*円", price_tag.get_text(" ") if price_tag else "")
        price_raw = price_match.group(1) if price_match else ""
        price_int = int(price_raw.replace(",", "")) if price_raw else 0

        stock_text = re.sub(r"^在庫\s*[:：]\s*", "", re.sub(r"\s+", " ", stock_tag.get_text(" ")).strip()) if stock_tag else ""
        stock_num = re.search(r"\d+", stock_text)
        in_stock = (int(stock_num.group()) > 0) if stock_num else ("×" not in stock_text and bool(stock_text))
        # Sold-out cards are rendered without the "sale" class / with a disabled cart button
        if card.select_one(".cart_sell_in.disabled") or "sold" in " ".join(card.get("class", [])):
            in_stock = False

        results.append(CardListing(
            site=SITE,
            card_no=found_no,
            rarity=rarity,
            name=name_tag.get_text(strip=True) if name_tag else "",
            price_int=price_int,
            price_raw=price_raw,
            stock_text=stock_text,
            in_stock=in_stock,
            url=link["href"] if link else SEARCH_URL,
            image_url=img.get("src", "") if img else "",
        ))
    return results
