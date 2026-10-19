"""
Technical analysis of XAU/USD.

``analyze_technicals`` turns raw OHLC data into a ``TechSnapshot``: a set of
indicator readings plus a single technical score from -100 (strongly
bearish) to +100 (strongly bullish) with the reasons behind it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from market import indicators as ind
from market.data import MarketData


@dataclass
class TechSnapshot:
    price: float
    change_1h_pct: float
    change_24h_pct: float
    change_7d_pct: float

    ema20: float
    ema50: float
    ema200: float
    daily_ema50: float
    daily_ema200: float

    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    macd_hist_prev: float
    atr: float
    bb_pct: float          # 0 = lower band, 1 = upper band
    bb_width: float
    bb_squeeze: bool       # bands unusually tight vs. recent history

    supports: list[float]
    resistances: list[float]
    pivots: dict[str, float]

    score: int = 0                       # -100 .. +100
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    # ---- convenience -------------------------------------------------------
    @property
    def trend_label(self) -> str:
        if self.score >= 50:
            return "Strong uptrend"
        if self.score >= 20:
            return "Uptrend"
        if self.score <= -50:
            return "Strong downtrend"
        if self.score <= -20:
            return "Downtrend"
        return "Range / mixed"

    def nearest_support(self) -> float | None:
        below = [s for s in self.supports if s < self.price]
        return max(below) if below else None

    def nearest_resistance(self) -> float | None:
        above = [r for r in self.resistances if r > self.price]
        return min(above) if above else None


def _last(series: pd.Series, offset: int = 0) -> float:
    clean = series.dropna()
    if len(clean) <= offset:
        return float("nan")
    return float(clean.iloc[-1 - offset])


def _clamp(x: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def analyze_technicals(data: MarketData) -> TechSnapshot:
    h = data.hourly
    d = data.daily
    close = h["close"]

    ema20 = ind.ema(close, 20)
    ema50 = ind.ema(close, 50)
    ema200 = ind.ema(close, 200)
    d_ema50 = ind.ema(d["close"], 50)
    d_ema200 = ind.ema(d["close"], 200)
    rsi = ind.rsi(close, 14)
    macd = ind.macd(close)
    atr = ind.atr(h, 14)
    bb = ind.bollinger(close, 20, 2.0)
    supports, resistances = ind.swing_levels(h, lookback=160, window=5)
    pivots = ind.pivot_points(d.iloc[-2]) if len(d) >= 2 else {}

    price = float(close.iloc[-1])
    width_hist = bb["width"].dropna().tail(100)
    bb_squeeze = bool(len(width_hist) >= 50 and width_hist.iloc[-1] < 0.6 * float(width_hist.median()))
    snap = TechSnapshot(
        price=price,
        change_1h_pct=data.change_pct(1),
        change_24h_pct=data.change_pct(24),
        change_7d_pct=data.change_pct(24 * 5),  # ~5 trading days of hourly bars
        ema20=_last(ema20),
        ema50=_last(ema50),
        ema200=_last(ema200),
        daily_ema50=_last(d_ema50),
        daily_ema200=_last(d_ema200),
        rsi=_last(rsi),
        macd=_last(macd["macd"]),
        macd_signal=_last(macd["signal"]),
        macd_hist=_last(macd["hist"]),
        macd_hist_prev=_last(macd["hist"], 1),
        atr=_last(atr),
        bb_pct=_last(bb["pct_b"]),
        bb_width=_last(bb["width"]),
        bb_squeeze=bb_squeeze,
        supports=supports,
        resistances=resistances,
        pivots=pivots,
    )
    _score(snap, ema20_slope=ind.slope_pct(ema20, 12))
    return snap


def _score(s: TechSnapshot, ema20_slope: float) -> None:
    """Fill in ``score``, ``reasons`` and ``flags`` on the snapshot."""
    score = 0.0
    reasons: list[str] = []
    flags: list[str] = []

    # 1) Hourly EMA alignment (max ±30)
    align = 0
    align += 1 if s.price > s.ema20 else -1
    align += 1 if s.ema20 > s.ema50 else -1
    align += 1 if s.ema50 > s.ema200 else -1
    score += align * 10
    if align == 3:
        reasons.append("Price above EMA20 > EMA50 > EMA200 on H1 (bullish stack)")
    elif align == -3:
        reasons.append("Price below EMA20 < EMA50 < EMA200 on H1 (bearish stack)")
    elif align > 0:
        reasons.append("H1 EMAs mostly bullish, not fully aligned")
    elif align < 0:
        reasons.append("H1 EMAs mostly bearish, not fully aligned")

    # 2) Daily trend (max ±20)
    daily = 0
    if s.price > s.daily_ema50:
        daily += 1
    else:
        daily -= 1
    if s.daily_ema50 > s.daily_ema200:
        daily += 1
    else:
        daily -= 1
    score += daily * 10
    if daily == 2:
        reasons.append("Daily trend bullish (price > D-EMA50 > D-EMA200)")
    elif daily == -2:
        reasons.append("Daily trend bearish (price < D-EMA50 < D-EMA200)")

    # 3) EMA20 slope (max ±10)
    score += _clamp(ema20_slope * 20, -10, 10)
    if ema20_slope > 0.25:
        reasons.append(f"EMA20 rising ({ema20_slope:+.2f}% over 12 bars)")
    elif ema20_slope < -0.25:
        reasons.append(f"EMA20 falling ({ema20_slope:+.2f}% over 12 bars)")

    # 4) MACD (max ±15)
    if s.macd_hist > 0:
        score += 8
        if s.macd_hist > s.macd_hist_prev:
            score += 7
            reasons.append("MACD histogram positive and expanding")
        else:
            reasons.append("MACD histogram positive but fading")
    elif s.macd_hist < 0:
        score -= 8
        if s.macd_hist < s.macd_hist_prev:
            score -= 7
            reasons.append("MACD histogram negative and expanding")
        else:
            reasons.append("MACD histogram negative but fading")

    # 5) RSI (max ±10, with overbought/oversold flags that pull the other way)
    if s.rsi >= 70:
        score += 2
        flags.append(f"RSI overbought ({s.rsi:.0f}) – pullback risk")
    elif s.rsi >= 55:
        score += 10
        reasons.append(f"RSI {s.rsi:.0f}: bullish momentum")
    elif s.rsi > 45:
        reasons.append(f"RSI {s.rsi:.0f}: neutral momentum")
    elif s.rsi > 30:
        score -= 10
        reasons.append(f"RSI {s.rsi:.0f}: bearish momentum")
    else:
        score -= 2
        flags.append(f"RSI oversold ({s.rsi:.0f}) – bounce risk")

    # 6) Bollinger position (max ±5) + squeeze flag
    if s.bb_pct == s.bb_pct:  # not NaN
        score += _clamp((s.bb_pct - 0.5) * 10, -5, 5)
        if s.bb_pct > 1.0:
            flags.append("Price above upper Bollinger band (stretched)")
        elif s.bb_pct < 0.0:
            flags.append("Price below lower Bollinger band (stretched)")
    if s.bb_squeeze:
        flags.append("Bollinger squeeze – breakout may be coming")

    # 7) 24h momentum (max ±10)
    score += _clamp(s.change_24h_pct * 5, -10, 10)
    if abs(s.change_24h_pct) >= 1.0:
        reasons.append(f"24h move {s.change_24h_pct:+.2f}%")

    s.score = int(round(_clamp(score)))
    s.reasons = reasons
    s.flags = flags
