"""
Macro context for gold: the dollar, US yields and equity risk appetite.

Gold is priced in dollars and pays no yield, so a weaker dollar and falling
yields are tailwinds, a stronger dollar and rising yields headwinds; a risk-off
tone in equities adds a little safe-haven demand. This module turns three daily
series (dollar index, 10-year yield, S&P 500) into a small score and plain lines:

    "Dollar index 103.2 · below its 20-day average, down 1.1% in 5 days – tailwind"

The overlay is deliberately modest (±8 points of the technical score at most);
it is context, not a signal. Daily data from Yahoo, cached for an hour.
"""
from __future__ import annotations

import logging
import threading
import time

import pandas as pd

log = logging.getLogger(__name__)

SYMBOLS = {"dxy": "DX-Y.NYB", "us10y": "^TNX", "spx": "^GSPC"}
LABELS = {"dxy": "Dollar index", "us10y": "US 10-year yield", "spx": "S&P 500"}
# contribution to the gold score when the series is rising (falling = opposite sign)
WEIGHT_UP = {"dxy": -4, "us10y": -3, "spx": -1}
_cache: dict = {"at": 0.0, "closes": None}
_lock = threading.Lock()
TTL = 3600.0


def fetch_closes(period: str = "1y") -> dict[str, pd.Series]:
    import yfinance as yf

    out: dict[str, pd.Series] = {}
    for key, sym in SYMBOLS.items():
        try:
            df = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=False)
            s = df["Close"].dropna()
            idx = pd.to_datetime(s.index)
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
            s.index = idx.normalize()
            if len(s) >= 30:
                out[key] = s.astype(float)
        except Exception as exc:  # noqa: BLE001
            log.debug("macro %s failed: %s", sym, exc)
    return out


def cached_closes() -> dict[str, pd.Series] | None:
    with _lock:
        if _cache["closes"] is not None and time.time() - _cache["at"] < TTL:
            return _cache["closes"]
    closes = fetch_closes()
    with _lock:
        if closes:
            _cache["closes"], _cache["at"] = closes, time.time()
        return _cache["closes"]


def _state_one(key: str, s: pd.Series) -> dict | None:
    if len(s) < 25:
        return None
    ema20 = s.ewm(span=20, adjust=False).mean()
    last, ref = float(s.iloc[-1]), float(ema20.iloc[-1])
    chg5 = (last / float(s.iloc[-6]) - 1.0) * 100.0 if len(s) > 6 and float(s.iloc[-6]) else 0.0
    rising = last > ref
    score = WEIGHT_UP[key] if rising else -WEIGHT_UP[key]
    if abs(last / ref - 1.0) < 0.002:          # sitting on the average: no opinion
        score = 0
    return {"symbol": SYMBOLS[key], "last": round(last, 2), "ema20": round(ref, 2), "chg5_pct": round(chg5, 2),
            "trend": "up" if rising else "down", "score": score}


def macro_state(closes: dict[str, pd.Series] | None) -> dict | None:
    """Score and lines from the three series (each truncated to its last value)."""
    if not closes:
        return None
    parts: dict[str, dict] = {}
    for key, s in closes.items():
        st = _state_one(key, s)
        if st:
            parts[key] = st
    if not parts:
        return None
    score = int(sum(p["score"] for p in parts.values()))
    reasons, flags = [], []
    for key, p in parts.items():
        unit = "%" if key == "us10y" else ""
        tone = "tailwind for gold" if p["score"] > 0 else "headwind for gold" if p["score"] < 0 else "neutral for gold"
        reasons.append(f"{LABELS[key]} {p['last']:,.2f}{unit} · {'above' if p['trend'] == 'up' else 'below'} its 20-day average, "
                       f"{p['chg5_pct']:+.1f}% in 5 days – {tone}")
    if parts.get("dxy", {}).get("score", 0) < 0 and parts.get("us10y", {}).get("score", 0) < 0:
        flags.append("Dollar and yields both rising – the macro tide is against gold longs")
    elif parts.get("dxy", {}).get("score", 0) > 0 and parts.get("us10y", {}).get("score", 0) > 0:
        flags.append("Dollar and yields both falling – the macro tide favours gold")
    label = "supportive" if score >= 4 else "against" if score <= -4 else "mixed"
    return {"score": score, "label": label, "parts": parts, "reasons": reasons, "flags": flags,
            "as_of": max(s.index[-1] for s in closes.values()).strftime("%Y-%m-%d")}


def current_macro() -> dict | None:
    try:
        return macro_state(cached_closes())
    except Exception as exc:  # noqa: BLE001
        log.debug("macro unavailable: %s", exc)
        return None


def macro_table(start, end) -> pd.DataFrame | None:
    """Daily macro states for a replay: the state for day D uses closes strictly before D (no look-ahead)."""
    try:
        closes = fetch_closes(period="6y")
    except Exception as exc:  # noqa: BLE001
        log.warning("macro history unavailable: %s", exc)
        return None
    if not closes:
        return None
    days = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="1D", tz="UTC")
    rows = []
    for d in days:
        trimmed = {k: s[s.index < d] for k, s in closes.items()}
        trimmed = {k: s for k, s in trimmed.items() if len(s) >= 25}
        st = macro_state(trimmed) if trimmed else None
        rows.append({"day": d, "score": st["score"] if st else 0, "state": st})
    return pd.DataFrame(rows).set_index("day")
