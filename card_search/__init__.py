"""
Single-card lookup across shops (used by the /findprice command).

Each site module exposes ``search(card_no) -> List[CardListing]``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

MAX_CARDS = 5

# e.g. NIK/S135-001, UMA/W106-001SEC, BD/W63-001
_CARD_RE = re.compile(r"^([A-Z0-9]+)/([A-Z0-9]+)-([A-Z0-9]+)$")
# Rarity suffix is letters only, appended to the number part (001 -> 001SEC)
_SUFFIX_RE = re.compile(r"^[A-Z]*$")
_TRAILING_LETTER_RE = re.compile(r"[A-Z]$")


@dataclass
class CardListing:
    site: str
    card_no: str       # e.g. "NIK/S135-001SEC"
    rarity: str        # e.g. "SEC"
    name: str
    price_int: int     # 0 if unknown / sold out
    price_raw: str     # e.g. "49,800"
    stock_text: str    # e.g. "4", "◯", "品切れ"
    in_stock: bool
    url: str
    image_url: str = ""
    currency: str = "¥"


def normalize(card_no: str) -> str | None:
    """Uppercase / trim and validate. Returns None if the format is invalid."""
    card_no = card_no.strip().upper().replace("／", "/").replace("－", "-")
    return card_no if _CARD_RE.match(card_no) else None


def parse_card_list(text: str) -> Tuple[List[str], List[str]]:
    """Split user input into (valid, invalid) card numbers, de-duplicated in order."""
    tokens = [t for t in re.split(r"[\s,，、]+", text) if t]
    valid: List[str] = []
    invalid: List[str] = []
    for token in tokens:
        norm = normalize(token)
        if norm is None:
            if token not in invalid:
                invalid.append(token)
        elif norm not in valid:
            valid.append(norm)
    return valid, invalid


def matches(query: str, found_no: str) -> bool:
    """True if *found_no* is *query* itself, or *query* plus a rarity suffix.

    ``NIK/S135-001`` matches ``NIK/S135-001``, ``NIK/S135-001SEC``, ``NIK/S135-001SP``
    but not ``NIK/S135-0010``. A query that already carries a suffix only matches exactly.
    """
    found_no = found_no.strip().upper()
    if _TRAILING_LETTER_RE.search(query):
        # Query already carries a rarity suffix: "…001S" must not match "…001SEC"
        return found_no == query
    if not found_no.startswith(query):
        return False
    return bool(_SUFFIX_RE.match(found_no[len(query):]))


def rarity_of(query: str, found_no: str) -> str:
    """Rarity suffix of *found_no* relative to the base number ('' for the base card)."""
    m = _CARD_RE.match(found_no.strip().upper())
    if not m:
        return ""
    num = m.group(3)
    return re.sub(r"^[0-9]+", "", num) if re.match(r"^[0-9]+[A-Z]*$", num) else ""


def official_image_url(card_no: str) -> str:
    """Card image on ws-tcg.com: .../cardlist/n/nik_s135/nik_s135_001sec.png"""
    m = _CARD_RE.match(card_no.upper())
    if not m:
        return ""
    series, setno, num = (g.lower() for g in m.groups())
    return (
        "https://ws-tcg.com/wordpress/wp-content/images/cardlist/"
        f"{series[0]}/{series}_{setno}/{series}_{setno}_{num}.png"
    )
