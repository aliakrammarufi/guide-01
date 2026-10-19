"""Price-action read: swings, structure, key levels, imbalances, regime, confluence."""
from __future__ import annotations

import numpy as np
import pandas as pd

from market import indicators as ind
from market import structure as ps
from market.analysis import analyze_technicals
from market.data import MarketData
from signals.engine import plan_entry


def _bars(path, start="2026-09-01", freq="1h", seed=0):
    rng = np.random.default_rng(seed)
    close = np.asarray(path, float)
    n = len(close)
    o = np.r_[close[0], close[:-1]]
    h = np.maximum(o, close) + rng.uniform(0.2, 1.0, n)
    lo = np.minimum(o, close) - rng.uniform(0.2, 1.0, n)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": close, "volume": 100.0}, index=idx)


def test_swings_alternate_and_structure_reads_a_staircase():
    # a clean staircase: higher highs and higher lows
    leg = []
    base = 4000.0
    for k in range(8):
        leg += list(np.linspace(base + k * 20, base + k * 20 + 30, 8)) + list(np.linspace(base + k * 20 + 30, base + k * 20 + 12, 6))
    df = _bars(leg)
    sw = ps.swings(df, 3)
    kinds = [s.kind for s in sw]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))          # strictly alternating
    st = ps.structure(df, window=3, lookback=300, tol=1.0)
    assert st.trend == "bullish" and st.label == "HH · HL"
    down = _bars(list(reversed(leg)))
    st2 = ps.structure(down, window=3, lookback=300, tol=1.0)
    assert st2.trend == "bearish" and st2.label == "LH · LL"


def test_equal_highs_are_not_a_higher_high():
    seq = []
    for k in range(6):
        seq += list(np.linspace(4000, 4030, 8)) + list(np.linspace(4030, 4005 + k * 2, 6))   # same high, rising lows
    df = _bars(seq)
    st = ps.structure(df, window=3, lookback=300, tol=3.0)
    assert st.label.startswith("EQH")


def test_impulse_fibs_and_fvg_and_adx():
    up = [4012, 4008, 4004, 4001, 4000, 4002, 4005] + list(np.linspace(4005, 4100, 40)) + list(np.linspace(4100, 4070, 10))
    df = _bars(up)
    imp = ps.last_impulse(df, window=3, lookback=300)
    assert imp and imp["direction"] == "up" and imp["levels"]["50"] < imp["to"] and imp["levels"]["61.8"] < imp["levels"]["50"]
    # a gap: bar i low far above bar i-2 high
    g = _bars([4000] * 10 + [4050] * 10)
    gaps = ps.fair_value_gaps(g, lookback=50)
    assert any(x["kind"] == "bullish" for x in gaps)
    a = ps.adx(df, 14).dropna()
    assert len(a) and 0 <= float(a.iloc[-1]) <= 100


def test_key_levels_prev_day_and_vwap():
    df = _bars(list(np.linspace(4000, 4050, 72)), start="2026-10-13 00:00")   # Tue 00:00 → Thu 23:00
    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    kl = ps.key_levels(df, daily, None)
    assert kl["PDH"] == round(float(daily.iloc[-2]["high"]), 2) and kl["PDL"] == round(float(daily.iloc[-2]["low"]), 2)
    assert "VWAP" in kl and "ASIA_H" in kl and kl["ROUND_ABOVE"] > kl["ROUND_BELOW"]


def test_price_action_feeds_the_snapshot_and_zone_confluence():
    rng = np.random.default_rng(1)
    n = 900
    close = 4000 + np.cumsum(rng.normal(0.4, 3.0, n))
    df = _bars(close)
    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily = pd.concat([daily] * 8).reset_index(drop=True)
    daily.index = pd.date_range(end=df.index[-1].normalize(), periods=len(daily), freq="1D", tz="UTC")
    data = MarketData(hourly=df, daily=daily, source="t", fetched_at=df.index[-1].to_pydatetime())
    tech = analyze_technicals(data)
    assert tech.pa is not None and tech.pa.h1.trend in ("bullish", "bearish", "range")
    assert tech.regime in ("trending", "weak trend", "ranging")
    plan = plan_entry("long", tech)
    assert plan["zone_lo"] < plan["zone_hi"] and plan["stop"] < plan["zone_lo"]
    assert isinstance(plan["confluence"], list)
    assert plan["zone_hi"] - plan["zone_lo"] <= 1.6 * tech.atr + 1e-6        # the refinement never makes the zone absurdly deep
