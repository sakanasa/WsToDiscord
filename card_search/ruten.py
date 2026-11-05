"""Card lookup in the 牌塔 store on ruten.com.tw (露天拍賣).

Uses the JSON API behind the store's own front-end (undocumented, so it may change):
  1. storeinfo        -> numeric seller id for the store name
  2. core/seller/<id>/prod?q=...  -> matching item ids
  3. items/v2/list?gno=...        -> name, price (TWD), stock, images
  4. items/v2/<id>/spec           -> only for items with spec_type "R" (e.g. 微損 / 無損)

Items with specs carry a different price and stock per spec. ``goods_price`` is the
cheapest spec even when that spec has no stock, so it must not be paired with the
item's total ``num``; each spec is reported on its own instead.
Item titles look like "『牌塔』WS 勝利女神：妮姬 Vol.2 NIK/S135-001S SR⁂ 閃卡 …".
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import List

import requests

from card_search import CardListing, matches
from scrapers.hobbystation import HEADERS

logger = logging.getLogger(__name__)

STORE_NAME = "yinanliao"
STORE_URL = f"https://www.ruten.com.tw/store/{STORE_NAME}/"
STOREINFO_URL = f"https://rapi.ruten.com.tw/api/users/v1/index.php/{STORE_NAME}/storeinfo"
SEARCH_URL = "https://rtapi.ruten.com.tw/api/search/v3/index.php/core/seller/{seller_id}/prod"
ITEMS_URL = "https://rapi.ruten.com.tw/api/items/v2/list"
SPEC_URL = "https://rapi.ruten.com.tw/api/items/v2/{item_id}/spec"
ITEM_PAGE_URL = "https://www.ruten.com.tw/item/show?{item_id}"
SITE = "ruten"

_TITLE_RE = re.compile(
    r"(?P<no>[A-Z0-9]+/[A-Z0-9]+-[A-Z]*\d+[A-Z]*)(?:\s+(?P<rarity>[A-Za-z]+))?"
)


@lru_cache(maxsize=1)
def _seller_id() -> str:
    resp = requests.get(STOREINFO_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return str(resp.json()["data"]["user_id"])


def _fetch_specs(item_id: str) -> List[tuple[str, int, int]]:
    """Return (label, price, stock) for each purchasable spec of an item."""
    resp = requests.get(SPEC_URL.format(item_id=item_id), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    specs = resp.json()["data"].get("specs", {})
    result = []
    for spec in specs.values():
        if spec.get("childs"):  # two-level specs: only leaf specs carry price / stock
            continue
        if spec.get("spec_status") != "Y":
            continue
        label = spec.get("spec_name", "")
        parent = specs.get(spec.get("parent_id", "0"))
        if parent:
            label = f"{parent.get('spec_name', '')} {label}".strip()
        result.append((label, int(spec.get("spec_price") or 0), int(spec.get("spec_num") or 0)))
    return result


def search(card_no: str) -> List[CardListing]:
    resp = requests.get(
        SEARCH_URL.format(seller_id=_seller_id()),
        params={"q": card_no, "sort": "prc/ac", "limit": 40, "offset": 1},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    ids = [row["Id"] for row in resp.json().get("Rows", [])]
    if not ids:
        return []

    detail = requests.get(
        ITEMS_URL, params={"gno": ",".join(ids), "level": "simple"}, headers=HEADERS, timeout=30
    )
    detail.raise_for_status()

    results: List[CardListing] = []
    for item in detail.json().get("data", []):
        m = _TITLE_RE.search(item.get("name", "").upper())
        if not m or not matches(card_no, m.group("no")):
            continue

        image = (item.get("images") or {}).get("url") or []
        base = dict(
            site=SITE,
            card_no=m.group("no"),
            name=re.sub(r"^『[^』]*』\s*WS\s*", "", item["name"]).strip(),
            url=ITEM_PAGE_URL.format(item_id=item["id"]),
            image_url=image[0] if image else "",
            currency="NT$",
        )
        rarity = m.group("rarity") or ""
        available = bool(item.get("available", True))

        variants: List[tuple[str, int, int]] = []
        if item.get("spec_type") == "R":
            try:
                variants = _fetch_specs(item["id"])
            except (requests.RequestException, KeyError, ValueError) as e:
                logger.warning("ruten: spec lookup failed for %s: %s", item["id"], e)
        if not variants:
            # No specs (or lookup failed): the in-stock price range's minimum is the safest price
            price_range = item.get("goods_price_range") or {}
            price = int(price_range.get("min") or item.get("goods_price") or 0)
            variants = [("", price, int(item.get("num") or 0))]

        for spec_label, price, stock in variants:
            in_stock = stock > 0 and available
            results.append(CardListing(
                rarity=f"{rarity} {spec_label}".strip(),
                price_int=price,
                price_raw=f"{price:,}" if price else "",
                stock_text=str(stock) if in_stock else "品切れ",
                in_stock=in_stock,
                **base,
            ))
    return results
