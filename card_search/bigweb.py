"""Card lookup on bigweb.co.jp (BIGWEB).

The site is an Angular SPA; this uses the JSON API behind it
(``https://api.bigweb.co.jp/products?game_id=126&name=...``, undocumented, may change).

``name`` is a substring match on "<series>/<set>-<fname>". Foil/parallel versions have a
zero-padded fname ("001SEC") while base cards do not ("1", "28"), so a base number needs a
second, unpadded query. Sold-out items are listed with price 0.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List

import requests

from card_search import CardListing, matches
from scrapers.hobbystation import HEADERS

logger = logging.getLogger(__name__)

API_URL = "https://api.bigweb.co.jp/products"
ITEM_URL = "https://www.bigweb.co.jp/ja/products/ws/cardViewer/{item_id}"
WS_GAME_ID = 126
MAX_PAGES = 4
SITE = "bigweb"

_QUERY_RE = re.compile(r"^(?P<series>[A-Z0-9]+)/(?P<set>[A-Z0-9]+)-(?P<pre>[A-Z]*)(?P<digits>\d+)(?P<suf>[A-Z]*)$")
_FNAME_RE = re.compile(r"^(?P<pre>[A-Z]*)(?P<digits>\d+)(?P<suf>[A-Z]*)$")


def _fetch(name: str) -> List[dict]:
    items: List[dict] = []
    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(
            API_URL,
            params={"game_id": WS_GAME_ID, "name": name, "page": page},
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("items", []))
        if not data.get("pagenate", {}).get("nextPage"):
            break
    return items


def search(card_no: str) -> List[CardListing]:
    q = _QUERY_RE.match(card_no)
    if not q:
        return []
    series, set_code = q.group("series"), q.group("set")
    q_pre, q_digits, q_suf = q.group("pre"), q.group("digits"), q.group("suf")

    queries = [card_no]
    if not q_suf:
        # Base cards use an unpadded number: NIK/S135-001 -> NIK/S135-1
        unpadded = f"{series}/{set_code}-{q_pre}{int(q_digits)}"
        if unpadded != card_no:
            queries.append(unpadded)

    items: Dict[int, dict] = {}
    for name in queries:
        for item in _fetch(name):
            items.setdefault(item["id"], item)

    results: List[CardListing] = []
    for item in items.values():
        f = _FNAME_RE.match(str(item.get("fname", "")).upper())
        if not f or f.group("pre") != q_pre or int(f.group("digits")) != int(q_digits):
            continue
        # Rebuild the number in the query's own (padded) form so it lines up with other shops
        found_no = f"{series}/{set_code}-{q_pre}{q_digits}{f.group('suf')}"
        if not matches(card_no, found_no):
            continue

        price = int(item.get("price") or 0)
        stock = int(item.get("stock_count") or 0)
        in_stock = not item.get("is_sold_out") and stock > 0 and price > 0
        results.append(CardListing(
            site=SITE,
            card_no=found_no,
            rarity=(item.get("rarity") or {}).get("web", ""),
            name=item.get("name", ""),
            price_int=price,
            price_raw=f"{price:,}" if price else "",
            stock_text=str(stock) if in_stock else "品切れ",
            in_stock=in_stock,
            url=ITEM_URL.format(item_id=item["id"]),
            image_url=item.get("image", ""),
        ))
    return results
