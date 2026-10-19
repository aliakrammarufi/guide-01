"""
Synthetic market data – used by the test-suite and by ``--demo`` mode so the
dashboard and bot can be tried without any API keys or internet access.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from market.data import MarketData


def make_ohlc(n: int, start: float, drift: float, vol: float, freq: str, seed: int = 7,
              end: datetime | None = None) -> pd.DataFrame:
    """Random-walk OHLC bars with a controllable drift (per bar, in %)."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift / 100.0, vol / 100.0, size=n)
    close = start * np.cumprod(1.0 + rets)
    open_ = np.concatenate([[start], close[:-1]])
    spread = np.abs(rng.normal(0, vol / 100.0, size=n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    end = end or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    idx = pd.date_range(end=end, periods=n, freq=freq)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0},
        index=idx,
    )


def make_market(drift_h: float = 0.01, vol: float = 0.15, seed: int = 7, bars: int = 6000,
                start: float = 4000.0, end: datetime | None = None) -> MarketData:
    """Hourly random walk; daily bars are resampled from it so both agree."""
    hourly = make_ohlc(bars, start, drift_h, vol, "h", seed, end=end)
    daily = hourly.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return MarketData(hourly=hourly, daily=daily, source="synthetic-demo", fetched_at=datetime.now(timezone.utc))
