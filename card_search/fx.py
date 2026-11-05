"""TWD -> JPY exchange rate, used to compare 露天 (NT$) prices with the Japanese shops (¥)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Tuple

import requests

logger = logging.getLogger(__name__)

CACHE_SECONDS = 6 * 3600
FALLBACK_RATE = 4.8  # JPY per 1 TWD, used only if every source fails

_SOURCES = [
    ("https://open.er-api.com/v6/latest/TWD", lambda j: j["rates"]["JPY"]),
    (
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/twd.json",
        lambda j: j["twd"]["jpy"],
    ),
]

_lock = threading.Lock()
_cache: dict = {"rate": None, "fetched_at": 0.0}


def twd_to_jpy() -> Tuple[float, bool]:
    """Return (JPY per 1 TWD, is_live). ``is_live`` is False when the fallback constant is used."""
    with _lock:
        if _cache["rate"] and time.time() - _cache["fetched_at"] < CACHE_SECONDS:
            return _cache["rate"], True
        for url, extract in _SOURCES:
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                rate = float(extract(resp.json()))
                if rate > 0:
                    _cache.update(rate=rate, fetched_at=time.time())
                    return rate, True
            except (requests.RequestException, KeyError, TypeError, ValueError) as e:
                logger.warning("fx: %s failed: %s", url.split("/")[2], e)
        if _cache["rate"]:  # stale but real beats the constant
            return _cache["rate"], True
        return FALLBACK_RATE, False
