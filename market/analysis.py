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
from market.structure import PriceAction, analyze_price_action


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
    pa: PriceAction | None = None        # price-action read (structure, key levels, regime…)

    # ---- price-action shortcuts --------------------------------------------
    @property
    def key_levels(self) -> dict:
        return self.pa.levels if self.pa else {}

    @property
    def regime(self) -> str:
        return self.pa.regime if self.pa else "unknown"

    def levels_around(self, max_dist_atr: float = 3.0) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Named key levels below and above the price, nearest first, within ``max_dist_atr`` ATRs."""
        below, above = [], []
        for name, v in self.key_levels.items():
            if name.startswith("ROUND"):
                name = "Round"
            if v is None:
                continue
            if v < self.price and self.price - v <= max_dist_atr * self.atr:
                below.append((name, float(v)))
            elif v > self.price and v - self.price <= max_dist_atr * self.atr:
                above.append((name, float(v)))
        below.sort(key=lambda t: -t[1])
        above.sort(key=lambda t: t[1])
        return below, above

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
    """The last non-NaN value (``offset`` = 0), or the one before it (1) – without a full dropna pass."""
    a = series.to_numpy(dtype=float)
    i = len(a) - 1
    seen = 0
    while i >= 0:
        if a[i] == a[i]:
            if seen == offset:
                return float(a[i])
            seen += 1
        i -= 1
    return float("nan")


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
    try:
        snap.pa = analyze_price_action(h, d, data.m15, snap.atr if snap.atr == snap.atr else 1.0, atr_series=atr, rsi_series=rsi)
    except Exception:  # noqa: BLE001 – the read is descriptive; the score must never depend on it crashing
        snap.pa = None
    _score(snap, ema20_slope=ind.slope_pct(ema20, 12))
    return snap


def _score(s: TechSnapshot, ema20_slope: float) -> None:
    """Fill in ``score``, ``reasons`` and ``flags`` on the snapshot.

    Trend-following parts (EMA stack, daily trend, structure, slope) are discounted when ADX says the
    market is ranging; divergence and the daily VWAP nudge the score; key levels only add flags.
    """
    reasons: list[str] = []
    flags: list[str] = []
    pa = s.pa
    trend = 0.0      # components that assume a trend
    other = 0.0      # momentum / position components

    # 1) Hourly EMA alignment (max ±24)
    align = 0
    align += 1 if s.price > s.ema20 else -1
    align += 1 if s.ema20 > s.ema50 else -1
    align += 1 if s.ema50 > s.ema200 else -1
    trend += align * 8
    if align == 3:
        reasons.append("Price above EMA20 > EMA50 > EMA200 on H1 (bullish stack)")
    elif align == -3:
        reasons.append("Price below EMA20 < EMA50 < EMA200 on H1 (bearish stack)")
    elif align > 0:
        reasons.append("H1 EMAs mostly bullish, not fully aligned")
    elif align < 0:
        reasons.append("H1 EMAs mostly bearish, not fully aligned")

    # 2) Daily trend (max ±16)
    daily = 0
    daily += 1 if s.price > s.daily_ema50 else -1
    daily += 1 if s.daily_ema50 > s.daily_ema200 else -1
    trend += daily * 8
    if daily == 2:
        reasons.append("Daily trend bullish (price > D-EMA50 > D-EMA200)")
    elif daily == -2:
        reasons.append("Daily trend bearish (price < D-EMA50 < D-EMA200)")

    # 3) Market structure H1 (±12, BOS +4, CHoCH −6) and H4 (±10)
    if pa is not None:
        h1, h4 = pa.h1, pa.h4
        if h1.trend == "bullish":
            trend += 12
            reasons.append(f"H1 structure bullish ({h1.label})")
        elif h1.trend == "bearish":
            trend -= 12
            reasons.append(f"H1 structure bearish ({h1.label})")
        else:
            reasons.append(f"H1 structure mixed ({h1.label}) – range")
        if h1.bos:
            trend += 4 if h1.bos == "up" else -4
            reasons.append(f"Break of structure {h1.bos} through {h1.bos_level:,.1f}")
        if h1.choch:
            trend += 6 if h1.choch == "up" else -6
            flags.append(f"Change of character {h1.choch} through {h1.choch_level:,.1f} – the H1 trend is being challenged")
        if h4.trend == "bullish":
            trend += 10
            reasons.append(f"H4 structure bullish ({h4.label})")
        elif h4.trend == "bearish":
            trend -= 10
            reasons.append(f"H4 structure bearish ({h4.label})")
        if h4.choch:
            flags.append(f"H4 change of character {h4.choch} at {h4.choch_level:,.1f}")

    # 4) EMA20 slope (max ±8)
    trend += _clamp(ema20_slope * 16, -8, 8)
    if ema20_slope > 0.25:
        reasons.append(f"EMA20 rising ({ema20_slope:+.2f}% over 12 bars)")
    elif ema20_slope < -0.25:
        reasons.append(f"EMA20 falling ({ema20_slope:+.2f}% over 12 bars)")

    # 5) MACD (max ±12)
    if s.macd_hist > 0:
        other += 6
        if s.macd_hist > s.macd_hist_prev:
            other += 6
            reasons.append("MACD histogram positive and expanding")
        else:
            reasons.append("MACD histogram positive but fading")
    elif s.macd_hist < 0:
        other -= 6
        if s.macd_hist < s.macd_hist_prev:
            other -= 6
            reasons.append("MACD histogram negative and expanding")
        else:
            reasons.append("MACD histogram negative but fading")

    # 6) RSI (max ±8, with overbought/oversold flags that pull the other way)
    if s.rsi >= 70:
        other += 2
        flags.append(f"RSI overbought ({s.rsi:.0f}) – pullback risk")
    elif s.rsi >= 55:
        other += 8
        reasons.append(f"RSI {s.rsi:.0f}: bullish momentum")
    elif s.rsi > 45:
        reasons.append(f"RSI {s.rsi:.0f}: neutral momentum")
    elif s.rsi > 30:
        other -= 8
        reasons.append(f"RSI {s.rsi:.0f}: bearish momentum")
    else:
        other -= 2
        flags.append(f"RSI oversold ({s.rsi:.0f}) – bounce risk")

    # 7) Bollinger position (max ±4) + squeeze flag
    if s.bb_pct == s.bb_pct:  # not NaN
        other += _clamp((s.bb_pct - 0.5) * 8, -4, 4)
        if s.bb_pct > 1.0:
            flags.append("Price above upper Bollinger band (stretched)")
        elif s.bb_pct < 0.0:
            flags.append("Price below lower Bollinger band (stretched)")
    if s.bb_squeeze:
        flags.append("Bollinger squeeze – breakout may be coming")

    # 8) 24h momentum (max ±6)
    other += _clamp(s.change_24h_pct * 3, -6, 6)
    if abs(s.change_24h_pct) >= 1.0:
        reasons.append(f"24h move {s.change_24h_pct:+.2f}%")

    # 9) Regime: a ranging market discounts every trend-following part
    if pa is not None and pa.adx == pa.adx:
        if pa.adx < 18:
            trend *= 0.6
            flags.append(f"ADX {pa.adx:.0f} – ranging market, trend signals discounted")
        elif pa.adx >= 25:
            reasons.append(f"ADX {pa.adx:.0f} – trending market")
        if pa.atr_pct >= 85:
            flags.append(f"Volatility high (ATR in the top {100 - pa.atr_pct:.0f}% of recent readings) – wider stops, smaller size")
        elif pa.atr_pct <= 15:
            flags.append(f"Volatility very low (ATR {pa.atr_pct:.0f}th percentile) – expect a breakout, targets may be slow")

    score = trend + other

    # 10) Divergence (±8) and daily VWAP (±4)
    if pa is not None:
        if pa.divergence == "bearish":
            score -= 8
            flags.append("Bearish RSI divergence at the last swing high – momentum fading")
        elif pa.divergence == "bullish":
            score += 8
            flags.append("Bullish RSI divergence at the last swing low – selling fading")
        vwap = pa.levels.get("VWAP")
        if vwap:
            score += 4 if s.price > vwap else -4
            reasons.append(f"Price {'above' if s.price > vwap else 'below'} the daily VWAP {vwap:,.1f}")
        if pa.candle:
            reasons.append(pa.candle[0].upper() + pa.candle[1:])
        # key levels right next to price: a wall is a reason to wait, not a score
        below, above = s.levels_around(max_dist_atr=0.35)
        for name, lvl in above[:1]:
            flags.append(f"{_level_name(name)} {lvl:,.1f} is right above – resistance until it breaks")
        for name, lvl in below[:1]:
            flags.append(f"{_level_name(name)} {lvl:,.1f} is right below – support until it breaks")

    s.score = int(round(_clamp(score)))
    s.reasons = reasons
    s.flags = flags


LEVEL_NAMES = {"PDH": "Previous-day high", "PDL": "Previous-day low", "PWH": "Previous-week high", "PWL": "Previous-week low",
               "ASIA_H": "Asian-session high", "ASIA_L": "Asian-session low", "VWAP": "Daily VWAP", "Round": "Round number",
               "ROUND_BELOW": "Round number", "ROUND_ABOVE": "Round number"}


def _level_name(key: str) -> str:
    return LEVEL_NAMES.get(key, key)
