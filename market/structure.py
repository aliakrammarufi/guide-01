"""
Price-action analysis: the things a discretionary gold trader reads off the chart
that moving averages don't show.

* **Market structure** – swing highs/lows (fractals), the sequence of higher
  highs / higher lows (bullish) or lower highs / lower lows (bearish), the last
  *break of structure* (BOS, trend continuation) and *change of character*
  (CHoCH, first break against the trend), on H1 and on H4 (built from H1).
* **Impulse + Fibonacci** – the last impulsive leg and its 38.2 / 50 / 61.8 / 78.6 %
  retracements, which is where pullback entries cluster.
* **Key levels** – previous day / week high & low, the Asian-session range, the
  daily VWAP (volume-weighted average price of the current session), round
  numbers (every $50): the levels gold reacts to and where stops sit.
* **Imbalances** – unfilled fair-value gaps (3-candle gaps) and order blocks (the
  last opposing candle before a structure break) near price: zone candidates.
* **Regime** – ADX (trend strength vs range) and the ATR percentile (quiet vs
  volatile), which decide how much to trust trend-following signals.
* **Divergence** – RSI making a lower high while price makes a higher high (and
  vice versa) at the last two swings: an exhaustion warning.
* **Candle read** – what the last closed H1 candle says (engulfing, rejection
  wick, inside bar).

Everything is plain pandas; nothing here is a prediction, it is a description.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd

from market import indicators as ind


# ---------------------------------------------------------------------------
# swings and structure
# ---------------------------------------------------------------------------
@dataclass
class Swing:
    kind: str          # "H" | "L"
    price: float
    index: int         # position in the frame
    time: pd.Timestamp


def swings(df: pd.DataFrame, window: int = 3) -> list[Swing]:
    """Fractal swing points: a high (low) that is the extreme of ``window`` bars on both sides (vectorised)."""
    highs, lows = df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float)
    n = len(df)
    w = window
    if n < 2 * w + 1:
        return []
    from numpy.lib.stride_tricks import sliding_window_view

    hv = sliding_window_view(highs, 2 * w + 1)          # row i ↔ centre bar i + w
    lv = sliding_window_view(lows, 2 * w + 1)
    ch, cl = hv[:, w], lv[:, w]
    is_high = (ch >= hv[:, :w].max(axis=1)) & (ch > hv[:, w + 1:].max(axis=1))
    is_low = (cl <= lv[:, :w].min(axis=1)) & (cl < lv[:, w + 1:].min(axis=1))
    idx = df.index
    out: list[Swing] = []
    for i in np.flatnonzero(is_high | is_low):
        pos = int(i) + w
        if is_high[i]:
            out.append(Swing("H", float(highs[pos]), pos, idx[pos]))
        if is_low[i]:
            out.append(Swing("L", float(lows[pos]), pos, idx[pos]))
    out.sort(key=lambda s: (s.index, s.kind))
    # alternate H / L: keep the more extreme of consecutive same-kind swings
    cleaned: list[Swing] = []
    for sw in out:
        if cleaned and cleaned[-1].kind == sw.kind:
            if (sw.kind == "H" and sw.price >= cleaned[-1].price) or (sw.kind == "L" and sw.price <= cleaned[-1].price):
                cleaned[-1] = sw
            continue
        cleaned.append(sw)
    return cleaned


@dataclass
class Structure:
    trend: str = "range"             # bullish | bearish | range
    label: str = ""                  # "HH · HL" etc.
    last_high: float | None = None   # most recent swing high
    last_low: float | None = None
    prev_high: float | None = None
    prev_low: float | None = None
    bos: str = ""                    # "up" | "down" | ""  – last break of structure (continuation)
    bos_level: float | None = None
    choch: str = ""                  # "up" | "down" | "" – change of character (first break against the trend)
    choch_level: float | None = None
    swings_used: int = 0

    def to_dict(self) -> dict:
        return {"trend": self.trend, "label": self.label, "last_high": self.last_high, "last_low": self.last_low,
                "bos": self.bos, "bos_level": self.bos_level, "choch": self.choch, "choch_level": self.choch_level}


def _cmp(a: float, b: float, tol: float) -> int:
    """+1 when ``a`` is meaningfully above ``b``, −1 below, 0 when equal within ``tol``."""
    return 1 if a > b + tol else -1 if a < b - tol else 0


def structure(df: pd.DataFrame, window: int = 3, lookback: int = 300, sw: list[Swing] | None = None,
              tol: float | None = None) -> Structure:
    """Classify the trend from the last swings and find the latest BOS / CHoCH.

    Equal highs/lows (within ``tol``, default 0.15 ATR) count as equal, not as a new high or low;
    the verdict uses the last two comparisons on each side, weighting the most recent one double.
    """
    d = df.tail(lookback)
    sw = swings(d, window) if sw is None else sw
    st = Structure(swings_used=len(sw))
    highs = [s for s in sw if s.kind == "H"]
    lows = [s for s in sw if s.kind == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return st
    if tol is None:
        a = ind.atr(d, 14).dropna()
        tol = 0.15 * float(a.iloc[-1]) if len(a) else 0.0
    st.last_high, st.prev_high = highs[-1].price, highs[-2].price
    st.last_low, st.prev_low = lows[-1].price, lows[-2].price
    hh1, hl1 = _cmp(highs[-1].price, highs[-2].price, tol), _cmp(lows[-1].price, lows[-2].price, tol)
    hh2 = _cmp(highs[-2].price, highs[-3].price, tol) if len(highs) >= 3 else 0
    hl2 = _cmp(lows[-2].price, lows[-3].price, tol) if len(lows) >= 3 else 0
    score = hh1 + hl1 + 0.5 * (hh2 + hl2)
    names_h = {1: "HH", 0: "EQH", -1: "LH"}
    names_l = {1: "HL", 0: "EQL", -1: "LL"}
    st.label = f"{names_h[hh1]} · {names_l[hl1]}"
    if score >= 1.5 and hl1 >= 0 and hh1 >= 0:
        st.trend = "bullish"
    elif score <= -1.5 and hh1 <= 0 and hl1 <= 0:
        st.trend = "bearish"
    else:
        st.trend = "range"
    # breaks: the structure point most recently traded through by a close, if any
    close = d["close"].to_numpy()
    events: list[tuple[int, str, float]] = []
    for s in highs[-3:]:
        after = close[s.index + 1:]
        idx = np.argmax(after > s.price) if (after > s.price).any() else -1
        if idx >= 0:
            events.append((s.index + 1 + int(idx), "up", s.price))
    for s in lows[-3:]:
        after = close[s.index + 1:]
        idx = np.argmax(after < s.price) if (after < s.price).any() else -1
        if idx >= 0:
            events.append((s.index + 1 + int(idx), "down", s.price))
    if events:
        events.sort()
        _when, direction, level = events[-1]
        # a break in the direction of the structure continues it (BOS); against it, it is a change of character
        if (direction == "up" and st.trend == "bullish") or (direction == "down" and st.trend == "bearish"):
            st.bos, st.bos_level = direction, level
        else:
            st.choch, st.choch_level = direction, level
    return st


def resample_h4(h1: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = h1.resample("4h", label="left", closed="left").agg(agg).dropna(subset=["close"])
    return out


# ---------------------------------------------------------------------------
# impulse + fibonacci
# ---------------------------------------------------------------------------
def last_impulse(df: pd.DataFrame, window: int = 3, lookback: int = 300, sw: list[Swing] | None = None) -> dict | None:
    """The last swing leg (low→high = up, high→low = down) and its retracement levels."""
    sw = swings(df.tail(lookback), window) if sw is None else sw
    if len(sw) < 2:
        return None
    a, b = sw[-2], sw[-1]
    if a.kind == b.kind:
        return None
    up = a.kind == "L" and b.kind == "H"
    lo, hi = (a.price, b.price) if up else (b.price, a.price)
    rng = hi - lo
    if rng <= 0:
        return None
    levels = {}
    for r in (0.382, 0.5, 0.618, 0.786):
        levels[f"{r * 100:.1f}".rstrip("0").rstrip(".")] = round(hi - rng * r, 2) if up else round(lo + rng * r, 2)
    return {"direction": "up" if up else "down", "from": round(lo if up else hi, 2), "to": round(hi if up else lo, 2),
            "size": round(rng, 2), "levels": levels, "start": a.time.isoformat(), "end": b.time.isoformat()}


# ---------------------------------------------------------------------------
# key levels
# ---------------------------------------------------------------------------
def key_levels(h1: pd.DataFrame, daily: pd.DataFrame, m15: pd.DataFrame | None = None) -> dict:
    """PDH/PDL, PWH/PWL, Asian range, daily VWAP, nearest round numbers."""
    out: dict = {}
    now = h1.index[-1]
    today = now.normalize()
    # previous completed day (a daily bar strictly before today)
    prev_days = daily[daily.index < today]
    if len(prev_days):
        pd_bar = prev_days.iloc[-1]
        out["PDH"], out["PDL"] = round(float(pd_bar["high"]), 2), round(float(pd_bar["low"]), 2)
    # previous completed week (Mon–Fri ending before this week's Monday)
    week_start = today - timedelta(days=today.weekday())
    prev_week = daily[(daily.index < week_start) & (daily.index >= week_start - timedelta(days=7))]
    if len(prev_week):
        out["PWH"], out["PWL"] = round(float(prev_week["high"].max()), 2), round(float(prev_week["low"].min()), 2)
    # Asian session range of the current day (00:00–07:00 UTC), from H1 bars
    asia = h1[(h1.index >= today) & (h1.index < today + timedelta(hours=7))]
    if len(asia) >= 2:
        out["ASIA_H"], out["ASIA_L"] = round(float(asia["high"].max()), 2), round(float(asia["low"].min()), 2)
    # daily VWAP from the finest bars available
    src = m15 if m15 is not None and len(m15) and "volume" in m15 else h1
    sess = src[src.index >= today]
    if len(sess) and float(sess["volume"].sum()) > 0:
        tp = (sess["high"] + sess["low"] + sess["close"]) / 3.0
        out["VWAP"] = round(float((tp * sess["volume"]).sum() / sess["volume"].sum()), 2)
    price = float(h1["close"].iloc[-1])
    step = 50.0
    below, above = np.floor(price / step) * step, np.ceil(price / step) * step
    if above == below:
        above += step
    out["ROUND_BELOW"], out["ROUND_ABOVE"] = float(below), float(above)
    return out


# ---------------------------------------------------------------------------
# imbalances: fair-value gaps and order blocks
# ---------------------------------------------------------------------------
def fair_value_gaps(df: pd.DataFrame, lookback: int = 120, min_size: float = 0.0) -> list[dict]:
    """Unfilled 3-candle gaps: bullish when bar[i-2].high < bar[i].low; bearish when bar[i-2].low > bar[i].high.
    A gap is 'filled' once a later bar trades through it."""
    d = df.tail(lookback)
    h, l = d["high"].to_numpy(), d["low"].to_numpy()
    out: list[dict] = []
    n = len(d)
    for i in range(2, n):
        if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= min_size:
            lo, hi = float(h[i - 2]), float(l[i])
            filled = bool((l[i + 1:] <= lo).any()) if i + 1 < n else False
            if not filled:
                out.append({"kind": "bullish", "lo": round(lo, 2), "hi": round(hi, 2), "time": d.index[i].isoformat()})
        elif h[i] < l[i - 2] and (l[i - 2] - h[i]) >= min_size:
            lo, hi = float(h[i]), float(l[i - 2])
            filled = bool((h[i + 1:] >= hi).any()) if i + 1 < n else False
            if not filled:
                out.append({"kind": "bearish", "lo": round(lo, 2), "hi": round(hi, 2), "time": d.index[i].isoformat()})
    return out[-6:]


def order_blocks(df: pd.DataFrame, lookback: int = 200, window: int = 3) -> list[dict]:
    """The last opposing candle before a move that broke structure – where the move was launched from.
    Bullish OB = last bearish candle before a close above the previous swing high; mirrored for bearish."""
    d = df.tail(lookback)
    sw = swings(d, window)
    o, c, h, l = d["open"].to_numpy(), d["close"].to_numpy(), d["high"].to_numpy(), d["low"].to_numpy()
    n = len(d)
    out: list[dict] = []
    for s in sw[-6:]:
        after_idx = np.arange(s.index + 1, n)
        if s.kind == "H":
            brk = after_idx[c[s.index + 1:] > s.price]
            if len(brk) == 0:
                continue
            b = int(brk[0])
            # last bearish candle before the break
            cand = [i for i in range(max(0, b - 12), b) if c[i] < o[i]]
            if not cand:
                continue
            i = cand[-1]
            mitigated = bool((l[b + 1:] <= h[i]).any()) if b + 1 < n else False
            if not mitigated:
                out.append({"kind": "bullish", "lo": round(float(l[i]), 2), "hi": round(float(h[i]), 2), "time": d.index[i].isoformat()})
        else:
            brk = after_idx[c[s.index + 1:] < s.price]
            if len(brk) == 0:
                continue
            b = int(brk[0])
            cand = [i for i in range(max(0, b - 12), b) if c[i] > o[i]]
            if not cand:
                continue
            i = cand[-1]
            mitigated = bool((h[b + 1:] >= l[i]).any()) if b + 1 < n else False
            if not mitigated:
                out.append({"kind": "bearish", "lo": round(float(l[i]), 2), "hi": round(float(h[i]), 2), "time": d.index[i].isoformat()})
    # de-duplicate overlapping blocks, keep the most recent
    uniq: list[dict] = []
    for ob in reversed(out):
        if not any(u["kind"] == ob["kind"] and u["lo"] <= ob["hi"] and ob["lo"] <= u["hi"] for u in uniq):
            uniq.append(ob)
    return list(reversed(uniq))[-4:]


# ---------------------------------------------------------------------------
# regime, divergence, candle read
# ---------------------------------------------------------------------------
def _wilder(x: np.ndarray, period: int) -> np.ndarray:
    """Wilder smoothing (an EMA with alpha = 1/period, seeded with the first value), NaN-aware."""
    out = np.full(len(x), np.nan)
    alpha = 1.0 / period
    acc = None
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        acc = v if acc is None else acc + alpha * (v - acc)
        out[i] = acc
    return out


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX (numpy; identical to the pandas ewm(adjust=False) formulation)."""
    h, l, c = df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float), df["close"].to_numpy(dtype=float)
    n = len(df)
    if n < period + 2:
        return pd.Series(np.nan, index=df.index)
    up = np.diff(h, prepend=np.nan)
    down = -np.diff(l, prepend=np.nan)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev_c), np.abs(l - prev_c)]), axis=0)
    atr_w = _wilder(tr, period)
    atr_w[: period - 1] = np.nan                       # min_periods = period, as in the pandas formulation
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * _wilder(plus_dm, period) / atr_w
        minus_di = 100.0 * _wilder(minus_dm, period) / atr_w
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    out = _wilder(dx, period)
    out[: 2 * period - 2] = np.nan
    return pd.Series(out, index=df.index)


def atr_percentile(df: pd.DataFrame, period: int = 14, lookback: int = 200, atr_series: pd.Series | None = None) -> float:
    """Where the current ATR sits in its recent history (0–100)."""
    a = (ind.atr(df, period) if atr_series is None else atr_series).dropna().tail(lookback)
    if len(a) < 20:
        return 50.0
    cur = float(a.iloc[-1])
    return round(100.0 * float((a < cur).mean()), 0)


def rsi_divergence(df: pd.DataFrame, window: int = 3, lookback: int = 200, sw: list[Swing] | None = None,
                   rsi_series: pd.Series | None = None) -> str:
    """'bearish' when the last two swing highs rise while RSI falls; 'bullish' for lows; '' otherwise."""
    d = df.tail(lookback)
    r = (ind.rsi(d["close"], 14) if rsi_series is None else rsi_series.reindex(d.index)).to_numpy()
    sw = swings(d, window) if sw is None else sw
    highs = [s for s in sw if s.kind == "H"][-2:]
    lows = [s for s in sw if s.kind == "L"][-2:]
    last = sw[-1] if sw else None
    if last is not None and last.kind == "H" and len(highs) == 2:
        a, b = highs
        if b.price > a.price and not np.isnan(r[a.index]) and r[b.index] < r[a.index] - 2:
            return "bearish"
    if last is not None and last.kind == "L" and len(lows) == 2:
        a, b = lows
        if b.price < a.price and not np.isnan(r[a.index]) and r[b.index] > r[a.index] + 2:
            return "bullish"
    return ""


def candle_read(df: pd.DataFrame, atr_value: float) -> str:
    """One line about the last closed bar: engulfing, rejection wick, inside bar, or nothing special."""
    if len(df) < 3:
        return ""
    last, prev = df.iloc[-2], df.iloc[-3]      # -1 may be the in-progress bar
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    po, pc = float(prev["open"]), float(prev["close"])
    body, rng = abs(c - o), max(h - l, 1e-9)
    up = c > o
    if up and pc < po and c > po and o <= pc and body > abs(pc - po):
        return "bullish engulfing H1 candle"
    if not up and pc > po and c < po and o >= pc and body > abs(pc - po):
        return "bearish engulfing H1 candle"
    lower_wick, upper_wick = min(o, c) - l, h - max(o, c)
    if lower_wick >= 2 * body and (c - l) / rng >= 0.6 and rng > 0.6 * atr_value:
        return "H1 rejection wick from below (buyers defended)"
    if upper_wick >= 2 * body and (h - c) / rng >= 0.6 and rng > 0.6 * atr_value:
        return "H1 rejection wick from above (sellers defended)"
    if h <= float(prev["high"]) and l >= float(prev["low"]):
        return "inside bar (compression)"
    if rng > 1.8 * atr_value:
        return f"wide-range {'bullish' if up else 'bearish'} H1 candle ({rng:.0f} pts)"
    return ""


# ---------------------------------------------------------------------------
# bundle
# ---------------------------------------------------------------------------
@dataclass
class PriceAction:
    h1: Structure = field(default_factory=Structure)
    h4: Structure = field(default_factory=Structure)
    impulse: dict | None = None
    levels: dict = field(default_factory=dict)
    fvgs: list = field(default_factory=list)
    order_blocks: list = field(default_factory=list)
    adx: float = float("nan")
    atr_pct: float = 50.0
    divergence: str = ""
    candle: str = ""

    @property
    def regime(self) -> str:
        if self.adx != self.adx:
            return "unknown"
        return "trending" if self.adx >= 25 else "weak trend" if self.adx >= 18 else "ranging"

    def to_dict(self) -> dict:
        return {"h1": self.h1.to_dict(), "h4": self.h4.to_dict(), "impulse": self.impulse, "levels": self.levels,
                "fvgs": self.fvgs, "order_blocks": self.order_blocks,
                "adx": None if self.adx != self.adx else round(self.adx, 1), "regime": self.regime,
                "atr_pct": self.atr_pct, "divergence": self.divergence, "candle": self.candle}


_H4_CACHE: dict = {}      # (last H1 bar time floored to 4h, len) → Structure – the H4 read changes only every 4 hours


def analyze_price_action(h1: pd.DataFrame, daily: pd.DataFrame, m15: pd.DataFrame | None, atr_value: float,
                         atr_series: pd.Series | None = None, rsi_series: pd.Series | None = None) -> PriceAction:
    pa = PriceAction()
    d = h1.tail(300)
    tol = 0.15 * max(atr_value, 1e-6)
    try:
        sw = swings(d, 4)
    except Exception:  # noqa: BLE001
        sw = []
    try:
        pa.h1 = structure(d, window=4, lookback=300, sw=sw, tol=tol)
    except Exception:  # noqa: BLE001
        pass
    try:
        # H4 structure uses completed H4 bars only (a break is only known at the H4 close), so it changes every 4 h
        key = (h1.index[-1].floor("4h"), len(h1) // 4)
        cached = _H4_CACHE.get(key)
        if cached is None:
            h4 = resample_h4(h1.tail(720))
            if len(h4) and h4.index[-1] + timedelta(hours=4) > h1.index[-1] + timedelta(hours=1):
                h4 = h4.iloc[:-1]                      # drop the in-progress H4 bar
            cached = structure(h4, window=2, lookback=180, tol=2 * tol) if len(h4) >= 30 else Structure()
            if len(_H4_CACHE) > 64:
                _H4_CACHE.clear()
            _H4_CACHE[key] = cached
        pa.h4 = cached
    except Exception:  # noqa: BLE001
        pass
    try:
        pa.impulse = last_impulse(d, window=4, lookback=300, sw=sw)
    except Exception:  # noqa: BLE001
        pass
    try:
        pa.levels = key_levels(h1, daily, m15)
    except Exception:  # noqa: BLE001
        pass
    try:
        pa.fvgs = fair_value_gaps(h1, lookback=120, min_size=0.15 * atr_value)
        pa.order_blocks = order_blocks(h1, lookback=200)
    except Exception:  # noqa: BLE001
        pass
    try:
        a = adx(h1.tail(250), 14).dropna()
        pa.adx = float(a.iloc[-1]) if len(a) else float("nan")
        pa.atr_pct = atr_percentile(h1, atr_series=atr_series)
    except Exception:  # noqa: BLE001
        pass
    try:
        pa.divergence = rsi_divergence(d, window=4, lookback=300, sw=sw, rsi_series=rsi_series)
        pa.candle = candle_read(h1, atr_value)
    except Exception:  # noqa: BLE001
        pass
    return pa
