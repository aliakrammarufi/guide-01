import numpy as np
import pandas as pd

from market import indicators as ind
from market.analysis import analyze_technicals
from market.synthetic import make_ohlc
from tests.conftest import FIXED_END


def test_rsi_bounds_and_direction():
    up = pd.Series(np.linspace(100, 200, 60))
    down = pd.Series(np.linspace(200, 100, 60))
    assert ind.rsi(up).iloc[-1] > 90
    assert ind.rsi(down).iloc[-1] < 10
    noisy = pd.Series(100 + np.random.default_rng(0).normal(0, 1, 500)).cumsum()
    r = ind.rsi(noisy).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_ema_tracks_trend():
    s = pd.Series(np.linspace(10, 110, 300))
    e = ind.ema(s, 20)
    assert e.iloc[-1] < s.iloc[-1]            # lags a rising series
    assert e.iloc[-1] > e.iloc[-50]           # but rises with it


def test_macd_shape():
    s = pd.Series(np.sin(np.linspace(0, 20, 400)) * 10 + 100)
    m = ind.macd(s)
    assert list(m.columns) == ["macd", "signal", "hist"]
    assert m.dropna().shape[0] > 300


def test_atr_positive():
    df = make_ohlc(300, 4000, 0.0, 0.2, "h", seed=1, end=FIXED_END)
    a = ind.atr(df)
    assert (a.dropna() > 0).all()


def test_bollinger_pct_b():
    df = make_ohlc(300, 4000, 0.0, 0.2, "h", seed=2, end=FIXED_END)
    bb = ind.bollinger(df["close"])
    last = bb.dropna().iloc[-1]
    assert last["lower"] < last["mid"] < last["upper"]


def test_swing_levels_sorted_and_deduped():
    df = make_ohlc(400, 4000, 0.0, 0.3, "h", seed=4, end=FIXED_END)
    sup, res = ind.swing_levels(df)
    assert sup == sorted(sup) and res == sorted(res)
    for lv in (sup, res):
        for a, b in zip(lv, lv[1:]):
            assert (b - a) / a > 0.0015


def test_pivots():
    p = ind.pivot_points(pd.Series({"high": 110.0, "low": 90.0, "close": 100.0}))
    assert p["P"] == 100.0 and p["R1"] == 110.0 and p["S1"] == 90.0


def test_analyze_technicals_bull_vs_bear(bull_market, bear_market):
    bull = analyze_technicals(bull_market)
    bear = analyze_technicals(bear_market)
    # the daily trend component should separate the two regimes
    assert bull.daily_ema50 > bull.daily_ema200
    assert bear.daily_ema50 < bear.daily_ema200
    for snap in (bull, bear):
        assert -100 <= snap.score <= 100
        assert snap.atr > 0
        assert 0 <= snap.rsi <= 100
        assert snap.reasons


def test_deterministic_ramp_scores_bullish():
    """A clean, low-noise ramp must score clearly bullish; its mirror clearly bearish."""
    from market.data import MarketData
    from datetime import datetime, timezone

    def build(drift):
        hourly = make_ohlc(3000, 4000, drift, 0.02, "h", seed=9, end=FIXED_END)
        daily = hourly.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        return MarketData(hourly=hourly, daily=daily, source="t", fetched_at=datetime.now(timezone.utc))

    # A relentless ramp is overbought (RSI ~98), which deliberately caps the score,
    # so we assert a clear sign rather than an extreme value.
    assert analyze_technicals(build(+0.02)).score >= 30
    assert analyze_technicals(build(-0.02)).score <= -30
