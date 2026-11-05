"""Card lookup on c-labo-online.jp (カードラボ).

Same shop platform as マスターズスクウェア, but titles look like
"【WS】エレグ:ブーム・アンド・ショック【SR】NIK/S135-001S" (rarity in 【】, number last).
Some items are discounted lower-grade copies, e.g. "【WS】※プレイ用特価品※隠されし力…";
the note is kept in the rarity label so they are not mistaken for the regular price.
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

SEARCH_URL = "https://www.c-labo-online.jp/product-list"
SITE = "clabo"

_TITLE_RE = re.compile(
    r"^【WS】(?P<name>.*)【(?P<rarity>[^】]*)】(?P<no>[A-Z0-9]+/[A-Z0-9]+-[A-Z]*\d+[A-Z]*)\s*$"
)
_NOTE_RE = re.compile(r"※([^※]+)※")


def search(card_no: str) -> List[CardListing]:
    # Search by base number so suffixed queries (…001SEC) behave like the other shops
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
        m = _TITLE_RE.match(name_tag.get_text("", strip=True))
        if not m or not matches(card_no, m.group("no")):
            continue

        raw_name = m.group("name")
        notes = _NOTE_RE.findall(raw_name)
        name = _NOTE_RE.sub("", raw_name).strip()
        rarity = " ".join([m.group("rarity"), *notes])

        price_tag = li.select_one(".selling_price .figure")
        price_match = re.search(r"([\d,]+)", price_tag.get_text(" ") if price_tag else "")
        price_raw = price_match.group(1) if price_match else ""

        stock_str = li.select_one(".stock").get_text(" ", strip=True) if li.select_one(".stock") else ""
        stock_num = re.search(r"(\d+)", stock_str)
        in_stock = bool(stock_str) and "在庫なし" not in stock_str and li.select_one("button.cartinput") is not None
        img_box = li.select_one("[data-src]")

        results.append(CardListing(
            site=SITE,
            card_no=m.group("no"),
            rarity=rarity,
            name=name,
            price_int=int(price_raw.replace(",", "")) if price_raw else 0,
            price_raw=price_raw,
            stock_text=stock_num.group(1) if (in_stock and stock_num) else "品切れ",
            in_stock=in_stock,
            url=link["href"],
            image_url=img_box["data-src"] if img_box else "",
        ))
    return results
