"""Card lookup on hobbystation-single.jp (ホビーステーション)."""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from card_search import CardListing, matches
from scrapers.hobbystation import BASE_URL, _get, _parse_price

logger = logging.getLogger(__name__)

SEARCH_URL = f"{BASE_URL}/ws/product/list"
SITE = "hobbystation"

_NAME_RARITY_RE = re.compile(r"^(?P<name>.*?)[(（](?P<rarity>[^()（）]*)[)）]\s*$")


def search(card_no: str) -> List[CardListing]:
    # Shop numbers drop the series prefix: NIK/S135-001SP -> "WS-S135-001SP"
    query = card_no.split("/", 1)[1]
    req = requests.Request("GET", SEARCH_URL, params={"search_word": query, "match_type": "1"}).prepare()
    resp = _get(req.url)
    soup = BeautifulSoup(resp.text, "lxml")

    results: List[CardListing] = []
    for li in soup.find_all("li"):
        link = li.find("a", href=re.compile(r"/ws/product/detail/\d+"))
        if not link:
            continue
        label = li.find("div")
        shop_no = label.get_text(strip=True) if label else ""
        if not shop_no.startswith("WS-"):
            continue
        series = card_no.split("/", 1)[0]
        found_no = f"{series}/{shop_no[3:]}"
        if not matches(card_no, found_no):
            continue

        name_tag = li.select_one("div.list_product_Name_pc a")
        full_name = name_tag.get_text(strip=True) if name_tag else ""
        nm = _NAME_RARITY_RE.match(full_name)

        price_int, price_raw = 0, ""
        package = li.find("div", class_="packageDetail")
        if package:
            for text in package.strings:
                if "円" in text:
                    price_int, price_raw = _parse_price(text.strip())
                    if price_int:
                        break

        sold_out = li.find("img", alt=re.compile(r"SOLD\s*OUT", re.IGNORECASE)) is not None
        stock_match = re.search(r"在庫数[:：]\s*(\S+)", li.get_text(" "))
        in_stock = not sold_out
        stock_text = "品切れ" if sold_out else (stock_match.group(1) if stock_match else "有貨")

        img = li.find("img", src=re.compile(r"/upload/save_image/"))
        href = link["href"]
        results.append(CardListing(
            site=SITE,
            card_no=found_no,
            rarity=nm.group("rarity") if nm else "",
            name=nm.group("name") if nm else full_name,
            price_int=price_int,
            price_raw=price_raw,
            stock_text=stock_text,
            in_stock=in_stock,
            url=href if href.startswith("http") else BASE_URL + href,
            image_url=(BASE_URL + img["src"]) if img else "",
        ))
    return results
