"""
USD → account-currency rate for position sizing (cached for an hour).

Sources, in order: the free currency-api on jsDelivr (no key), Yahoo Finance
(``CAD=X`` style tickers), then a static fallback so sizing never breaks.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

log = logging.getLogger(__name__)

FALLBACK = {"USD": 1.0, "CAD": 1.41, "EUR": 0.92, "GBP": 0.78, "AUD": 1.52, "NZD": 1.68, "CHF": 0.88, "JPY": 150.0, "SGD": 1.34, "AED": 3.67}
_cache: dict[str, tuple[float, float, str]] = {}   # currency -> (rate, fetched_at, source)
_lock = threading.Lock()
TTL = 3600.0


def usd_rate(currency: str) -> tuple[float, str]:
    """How many units of ``currency`` one US dollar buys, and where the rate came from."""
    cur = (currency or "USD").upper()
    if cur == "USD":
        return 1.0, "fixed"
    with _lock:
        hit = _cache.get(cur)
        if hit and time.time() - hit[1] < TTL:
            return hit[0], hit[2]
    rate, source = _fetch(cur)
    with _lock:
        _cache[cur] = (rate, time.time(), source)
    return rate, source


def _fetch(cur: str) -> tuple[float, str]:
    try:
        r = httpx.get("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json", timeout=8)
        r.raise_for_status()
        v = r.json().get("usd", {}).get(cur.lower())
        if v:
            return float(v), "currency-api"
    except Exception as exc:  # noqa: BLE001
        log.debug("currency-api failed: %s", exc)
    try:
        import yfinance as yf
        fi = yf.Ticker(f"{cur}=X").fast_info
        v = fi.get("last_price")
        if v:
            return float(v), "yahoo"
    except Exception as exc:  # noqa: BLE001
        log.debug("yahoo fx failed: %s", exc)
    log.warning("Using fallback USD/%s rate", cur)
    return FALLBACK.get(cur, 1.0), "fallback"
