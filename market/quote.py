"""
Fast quotes for the live dashboard.

* ``get_quote()`` – the latest XAU/USD price (polled every few seconds by the
  dashboard; cached server-side so the provider is never hammered).
* ``get_tape()``  – a small set of related instruments for the ticker tape:
  dollar index, 10-year yield, silver, oil, S&P 500, EUR/USD, VIX.

Both fall back gracefully: on any provider error the last good value is
returned, and the dashboard shows the data as stale rather than breaking.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from config import settings

log = logging.getLogger(__name__)

TAPE_SYMBOLS: list[tuple[str, str, str]] = [
    # yfinance symbol, display name, format ("price" | "pct" | "yield")
    ("DX-Y.NYB", "DXY", "price"),
    ("^TNX", "US10Y", "yield"),
    ("SI=F", "SILVER", "price"),
    ("CL=F", "OIL", "price"),
    ("^GSPC", "S&P 500", "price"),
    ("EURUSD=X", "EUR/USD", "price"),
    ("^VIX", "VIX", "price"),
    ("BTC-USD", "BTC", "price"),
]


@dataclass
class Quote:
    price: float
    prev_close: float
    ts: float = field(default_factory=time.time)

    @property
    def change_pct(self) -> float:
        return (self.price / self.prev_close - 1.0) * 100.0 if self.prev_close else 0.0

    def to_dict(self) -> dict:
        return {"price": round(self.price, 2), "prev_close": round(self.prev_close, 2),
                "change_pct": round(self.change_pct, 3), "ts": self.ts}


class QuoteService:
    def __init__(self, demo: bool = False, min_interval: float | None = None, tape_interval: float = 60.0,
                 prev_close_fn: Callable[[float], float | None] | None = None):
        self.demo = demo
        self.min_interval = min_interval if min_interval is not None else max(0.5, settings.quote_interval_sec)
        self.tape_interval = tape_interval
        # Optional: previous close supplied by the analysis cache (same basis as the live tick)
        self._prev_close_fn = prev_close_fn
        self._prev_cached: float | None = None   # last good previous close from any source
        self._prev_yf_ts = 0.0                   # last time we asked yfinance for it
        self._lock = threading.Lock()
        self._quote: Quote | None = None
        self._tape: list[dict] = []
        self._tape_ts = 0.0
        self._rng = random.Random(42)
        self._demo_anchor: float | None = None
        self._demo_tape_state: dict[str, float] = {}

    # ---- demo helpers ------------------------------------------------------
    def set_demo_anchor(self, price: float) -> None:
        self._demo_anchor = price
        if self._quote is None:
            self._quote = Quote(price=price, prev_close=price * 0.995)

    def _demo_quote(self) -> Quote:
        base = self._quote.price if self._quote else (self._demo_anchor or 4500.0)
        anchor = self._demo_anchor or base
        # mean-reverting random walk so the demo never drifts away
        step = self._rng.gauss(0, 0.45) + (anchor - base) * 0.02
        price = round(base + step, 2)
        prev = self._quote.prev_close if self._quote else anchor * 0.995
        return Quote(price=price, prev_close=prev)

    def _demo_tape(self) -> list[dict]:
        seeds = {"DXY": 103.2, "US10Y": 4.12, "SILVER": 52.4, "OIL": 71.8, "S&P 500": 6410.0,
                 "EUR/USD": 1.0920, "VIX": 15.3, "BTC": 108500.0}
        out = []
        for _, name, kind in TAPE_SYMBOLS:
            base = self._demo_tape_state.get(name, seeds[name])
            price = base * (1 + self._rng.gauss(0, 0.0006))
            self._demo_tape_state[name] = price
            prev = seeds[name]
            out.append({"name": name, "price": price, "change_pct": (price / prev - 1) * 100, "kind": kind})
        return out

    # ---- live --------------------------------------------------------------
    def _goldapi_quote(self) -> Quote:
        r = httpx.get("https://api.gold-api.com/price/XAU", timeout=6, headers={"User-Agent": "XAUSentinel/1.0"})
        r.raise_for_status()
        price = float(r.json()["price"])
        return Quote(price=price, prev_close=self._prev_close(price))

    def _prev_close(self, price: float) -> float:
        """Previous daily close on the same basis as ``price``.

        1. the analysis cache (basis-adjusted, see web/server.py) – preferred
        2. yfinance fast_info, retried at most every 10 min
        3. the last good value we had; finally the price itself (0.00% change)
        """
        if self._prev_close_fn is not None:
            try:
                v = self._prev_close_fn(price)
                if v and v > 0:
                    self._prev_cached = float(v)
                    return self._prev_cached
            except Exception as exc:  # noqa: BLE001
                log.debug("prev_close_fn failed: %s", exc)
        now = time.time()
        if self._prev_cached is None and now - self._prev_yf_ts > 600:
            self._prev_yf_ts = now
            try:
                import yfinance as yf
                fi = yf.Ticker(settings.yf_symbol).fast_info
                v = fi.get("previous_close") or fi.get("regular_market_previous_close")
                if v:
                    self._prev_cached = float(v)
            except Exception as exc:  # noqa: BLE001
                log.debug("yfinance previous close failed: %s", exc)
        return self._prev_cached or price

    def _live_quote(self) -> Quote:
        if settings.quote_provider == "goldapi":
            try:
                return self._goldapi_quote()
            except Exception as exc:  # noqa: BLE001
                log.warning("gold-api quote failed (%s); using %s", exc, settings.data_provider)
        if (settings.quote_provider == "twelvedata" or settings.data_provider == "twelvedata") and settings.twelvedata_api_key:
            try:
                r = httpx.get("https://api.twelvedata.com/quote",
                              params={"symbol": settings.twelvedata_symbol, "apikey": settings.twelvedata_api_key},
                              timeout=10)
                d = r.json()
                return Quote(price=float(d["close"]), prev_close=float(d["previous_close"]))
            except Exception as exc:  # noqa: BLE001
                log.warning("Twelve Data quote failed (%s); using yfinance", exc)
        import yfinance as yf

        fi = yf.Ticker(settings.yf_symbol).fast_info
        price = float(fi["last_price"])
        prev = float(fi.get("previous_close") or fi.get("regular_market_previous_close") or price)
        return Quote(price=price, prev_close=prev)

    def _live_tape(self) -> list[dict]:
        import yfinance as yf

        out = []
        for sym, name, kind in TAPE_SYMBOLS:
            try:
                fi = yf.Ticker(sym).fast_info
                price = float(fi["last_price"])
                prev = float(fi.get("previous_close") or price)
                out.append({"name": name, "price": price, "change_pct": (price / prev - 1) * 100 if prev else 0.0, "kind": kind})
            except Exception as exc:  # noqa: BLE001
                log.debug("tape %s failed: %s", sym, exc)
        return out

    # ---- public ------------------------------------------------------------
    def get_quote(self) -> dict:
        with self._lock:
            now = time.time()
            if self._quote is None or now - self._quote.ts >= (0.8 if self.demo else self.min_interval):
                try:
                    self._quote = self._demo_quote() if self.demo else self._live_quote()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Quote failed: %s", exc)
            interval_ms = int((0.8 if self.demo else self.min_interval) * 1000)
            if self._quote is None:
                return {"price": None, "prev_close": None, "change_pct": None, "ts": now, "stale": True, "interval_ms": interval_ms}
            return self._quote.to_dict() | {"stale": now - self._quote.ts > 120, "interval_ms": interval_ms}

    def get_tape(self) -> list[dict]:
        with self._lock:
            now = time.time()
            if now - self._tape_ts >= (2.0 if self.demo else self.tape_interval):
                try:
                    tape = self._demo_tape() if self.demo else self._live_tape()
                    if tape:
                        self._tape, self._tape_ts = tape, now
                except Exception as exc:  # noqa: BLE001
                    log.warning("Tape failed: %s", exc)
            return [{**t, "price": round(t["price"], 4 if t["kind"] == "price" and t["price"] < 10 else 2),
                     "change_pct": round(t["change_pct"], 2)} for t in self._tape]
