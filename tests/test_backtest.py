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


def test_decode_dukascopy_candles_and_period_breakdown():
    from market.history import decode_candles
    payload = {"timestamp": 1700000000000, "multiplier": 0.001, "open": 1980.5, "high": 1981.0, "low": 1980.0, "close": 1980.8,
               "shift": 60000, "times": [0, 1, 2], "opens": [0, 300, -100], "highs": [0, 500, 0], "lows": [0, 200, -300],
               "closes": [0, 100, -200], "volumes": [1.0, 2.0, 3.0]}
    df = decode_candles(payload)
    assert len(df) == 3 and str(df.index[0]) == "2023-11-14 22:13:20+00:00"
    assert abs(df["open"].iloc[1] - 1980.8) < 1e-9 and abs(df["close"].iloc[2] - 1980.7) < 1e-9
    assert (df.index[2] - df.index[1]).total_seconds() == 120           # a gap of one minute, not filled
    from datetime import datetime, timedelta, timezone
    from signals.tracker import TradeSignal
    from signals.backtest import summarize, ReplayParams
    rows = []
    t0 = datetime(2024, 1, 10, tzinfo=timezone.utc)
    for i in range(12):
        opened = t0 + timedelta(days=40 * i)
        rows.append(TradeSignal(id=i + 1, side="BUY", entry=2000, sl=1990, tp1=2010, tp2=2020, confidence=50, opened_at=opened,
                                status="tp2" if i % 3 else "stopped", closed_at=opened + timedelta(hours=5), exit_price=2020 if i % 3 else 1990,
                                result_r=1.5 if i % 3 else -1.0, triggered_at=opened))
    res = summarize(rows, {}, ReplayParams())
    assert "by_year" in res and set(res["by_year"]) == {"2024", "2025"} and "by_quarter" in res


def test_applicable_variants_skip_the_current_rules_and_duplicates():
    from signals.backtest import VARIANTS, ReplayParams, applicable_variants, _brief

    base = ReplayParams(breakeven_after_tp1=True)
    names = [n for n, _o, _p in applicable_variants(base)]
    assert "break-even stop after TP1" not in names and "no break-even stop" in names
    assert "no break-even + break-only" in names                       # a real combination when the base has break-even
    assert "targets 1.5 / 3.0 ATR" not in names and "targets 1.25 / 2.5 ATR" in names    # 3.0 is the default
    base2 = ReplayParams(breakeven_after_tp1=False)
    names2 = [n for n, _o, _p in applicable_variants(base2)]
    assert "no break-even stop" not in names2 and "break-even stop after TP1" in names2
    assert "no break-even + break-only" not in names2                  # same as "structure-break confirmations only" now
    assert "structure-break confirmations only" in names2
    assert len(names2) < len(VARIANTS)
    b = _brief({"count": 3, "total_r": 1.0}, {"2025": {"count": 3, "win_rate": 66.7, "total_r": 1.0, "avg_r": 0.33}})
    assert b["by_year"] == {"2025": {"count": 3, "win_rate": 66.7, "total_r": 1.0}} and b["count"] == 3
