"""
Technical indicators implemented with plain pandas (no TA-Lib dependency).

All functions take a DataFrame with columns open/high/low/close and return
Series aligned to the same index.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When there were no losses at all, RSI is 100 by definition
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    line = fast_ema - slow_ema
    sig = ema(line.dropna(), signal).reindex(close.index)
    hist = line - sig
    return pd.DataFrame({"macd": line, "signal": sig, "hist": hist})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, window)
    std = close.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid
    # 0 = at lower band, 1 = at upper band
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": width, "pct_b": pct_b})


def swing_levels(df: pd.DataFrame, lookback: int = 120, window: int = 5) -> tuple[list[float], list[float]]:
    """Find recent swing highs (resistance) and swing lows (support).

    A swing high is a bar whose high is the maximum of the ``window`` bars on
    either side.  Returns (supports, resistances) sorted by proximity-friendly
    ascending price order, de-duplicated within 0.15 %.
    """
    recent = df.tail(lookback)
    highs = recent["high"].to_numpy()
    lows = recent["low"].to_numpy()
    n = len(recent)
    resistances: list[float] = []
    supports: list[float] = []
    for i in range(window, n - window):
        seg_h = highs[i - window : i + window + 1]
        seg_l = lows[i - window : i + window + 1]
        if highs[i] == seg_h.max():
            resistances.append(float(highs[i]))
        if lows[i] == seg_l.min():
            supports.append(float(lows[i]))

    def dedupe(levels: list[float]) -> list[float]:
        out: list[float] = []
        for lvl in sorted(levels):
            if not out or abs(lvl - out[-1]) / out[-1] > 0.0015:
                out.append(lvl)
        return out

    return dedupe(supports), dedupe(resistances)


def pivot_points(prev_bar: pd.Series) -> dict[str, float]:
    """Classic floor-trader pivots from the previous (daily) bar."""
    h, l, c = float(prev_bar["high"]), float(prev_bar["low"]), float(prev_bar["close"])
    p = (h + l + c) / 3.0
    return {
        "P": p,
        "R1": 2 * p - l,
        "S1": 2 * p - h,
        "R2": p + (h - l),
        "S2": p - (h - l),
    }


def slope_pct(series: pd.Series, bars: int) -> float:
    """Percent change of a series over the last ``bars`` points."""
    clean = series.dropna()
    if len(clean) <= bars:
        return 0.0
    a, b = float(clean.iloc[-1 - bars]), float(clean.iloc[-1])
    return (b / a - 1.0) * 100.0 if a else 0.0
