"""Historical replay: runs end to end on synthetic bars and produces a consistent report."""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals.backtest import ReplayParams, replay, summarize


def synth(days: int = 65, seed: int = 3):
    rng = np.random.default_rng(seed)
    n = days * 96
    idx = pd.date_range("2026-03-02", periods=n, freq="15min", tz="UTC")
    drift = np.where(np.arange(n) < n // 2, 0.09, -0.07)
    close = 3000 + np.cumsum(rng.normal(drift, 2.0, n))
    o = np.r_[close[0], close[:-1]]
    h = np.maximum(o, close) + rng.uniform(0, 2, n)
    lo = np.minimum(o, close) - rng.uniform(0, 2, n)
    m15 = pd.DataFrame({"open": o, "high": h, "low": lo, "close": close, "volume": 1.0}, index=idx)
    m15 = m15[m15.index.dayofweek < 5]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return m15.resample("1h").agg(agg).dropna(), m15.resample("1D").agg(agg).dropna(), m15


def test_replay_runs_and_report_is_consistent():
    h1, d1, m15 = synth()
    tail = m15[m15.index >= m15.index[-1] - pd.Timedelta(days=9)]
    res = replay(h1, d1, tail, ReplayParams(min_conf=45))
    s = res["summary"]
    assert res["period"]["bars_15m"] == len(tail)
    assert s["setups"] >= s["count"] + s["no_trade"]
    assert s["count"] == s["wins"] + s["losses"] + s["breakeven"]
    assert len(res["equity"]) == s["count"]
    assert sum(b["count"] for b in res["by_confidence"].values()) == s["count"]
    assert sum(b["count"] for b in res["by_session"].values()) == s["count"]
    assert res["thresholds"]["45"]["count"] == s["count"]            # min_conf 45 → every trade has conf ≥ 45
    for t in res["trades"]:
        assert t["conf"] >= 45 and t["status"] not in ("pending", "active", "tp1")
        if t["side"] == "BUY":
            assert t["sl"] < t["entry"] < t["tp1"] <= t["tp2"]
        else:
            assert t["tp2"] <= t["tp1"] < t["entry"] < t["sl"]
    assert "news" in res["note"]


def test_replay_needs_history():
    h1, d1, m15 = synth(days=8)
    try:
        replay(h1, d1, m15, ReplayParams())
    except ValueError as exc:
        assert "history" in str(exc)
    else:
        raise AssertionError("expected a ValueError for too little history")


def test_summarize_empty():
    res = summarize([], {}, ReplayParams())
    assert res["summary"]["count"] == 0 and res["summary"]["win_rate"] is None and res["equity"] == []


def test_bar_path_fills_at_levels_conservatively():
    from types import SimpleNamespace as N

    from signals.backtest import _bar_path

    buy = N(side="BUY", is_open=True, status="active", effective_sl=90.0, entry=100.0, tp1=110.0, tp2=120.0)
    assert _bar_path(buy, 89, 115, 100) == [90.0]                 # stop first, at the stop level
    assert _bar_path(buy, 101, 111, 105) == [110.0, 105]          # TP1 at its level, then the close
    assert _bar_path(buy, 95, 111, 105) == [110.0, 100.0]         # TP1 and back below entry → break-even stop
    assert _bar_path(buy, 101, 125, 118) == [110.0, 120.0]        # straight through to TP2
    tp1 = N(side="BUY", is_open=True, status="tp1", effective_sl=100.0, entry=100.0, tp1=110.0, tp2=120.0)
    assert _bar_path(tp1, 99.5, 121, 115) == [100.0]
    sell = N(side="SELL", is_open=True, status="active", effective_sl=110.0, entry=100.0, tp1=90.0, tp2=80.0)
    assert _bar_path(sell, 85, 99, 88) == [90.0, 88]
    assert _bar_path(N(side="SELL", is_open=False), 85, 111, 88) == [111, 85, 88]
