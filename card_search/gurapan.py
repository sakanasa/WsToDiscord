"""Card lookup on gurapan.jp (グランドパンダキャニオン, EC-CUBE shop).

Product titles do not contain the card number ("エレグ：ブーム・アンド・ショック【SR】"), so the number is
read from the thumbnail path (".../WS/S135/small/S135-001S_small.jpg"). The shop's keyword search
does match on the full code (series prefix included), so results are already scoped to the query.
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

BASE_URL = "https://gurapan.jp"
SEARCH_URL = f"{BASE_URL}/products/list"
SITE = "gurapan"

# "S135-001SEC", "SE54-02OLRe" (lowercase tail = errata version), "W106-T01R"
_IMG_CODE_RE = re.compile(r"/WS/[A-Z0-9]+/(?:small/)?(?P<set>[A-Z0-9]+)-(?P<num>[A-Z]*\d+)(?P<suf>[A-Z]*)(?P<tail>[a-z]*)_")
_TAG_RE = re.compile(r"【([^】]*)】")
# Parallel suffix -> rarity label (S is SR and R is RRR in the WS numbering scheme)
_SUFFIX_RARITY = {"S": "SR", "R": "RRR"}


def search(card_no: str) -> List[CardListing]:
    resp = requests.get(SEARCH_URL, params={"name": card_no}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    series = card_no.split("/", 1)[0]

    results: List[CardListing] = []
    for li in soup.select("li.ec-shelfGrid__item"):
        img = li.select_one("img")
        name_tag = li.select_one(".ec-shelfGrid__item-name")
        link = li.select_one("a[href]")
        if not img or not name_tag or not link:
            continue
        m = _IMG_CODE_RE.search(img.get("src", ""))
        if not m:
            continue
        found_no = f"{series}/{m.group('set')}-{m.group('num')}{m.group('suf')}"
        if not matches(card_no, found_no):
            continue

        title = name_tag.get_text(strip=True)
        tags = _TAG_RE.findall(title)
        name = _TAG_RE.sub("", title).strip()

        suffix = m.group("suf")
        rarity = _SUFFIX_RARITY.get(suffix, suffix) if suffix else (tags[0] if tags else "")
        # Keep non-rarity notes such as エラッタ前 / エラッタ後 so versions stay distinguishable
        notes = [t for t in tags[1:] if not re.fullmatch(r"[A-Z]+", t)]
        label = " ".join([rarity, *notes]).strip()

        price_match = re.search(r"[￥¥]\s*([\d,]+)", li.select_one(".price02-default").get_text() if li.select_one(".price02-default") else "")
        price_raw = price_match.group(1) if price_match else ""
        stock_tag = li.select_one(".ec-shelfGrid__item-stock")
        stock_match = re.search(r"(\d+)", stock_tag.get_text() if stock_tag else "")
        in_stock = (
            "SOLD OUT" not in li.get_text()
            and li.select_one("button.add-cart") is not None
            and bool(stock_match)
        )

        src = img["src"]
        results.append(CardListing(
            site=SITE,
            card_no=found_no,
            rarity=label,
            name=name,
            price_int=int(price_raw.replace(",", "")) if price_raw else 0,
            price_raw=price_raw,
            stock_text=stock_match.group(1) if (in_stock and stock_match) else "品切れ",
            in_stock=in_stock,
            url=link["href"],
            image_url=(BASE_URL + src) if src.startswith("/") else src,
        ))
    return results
