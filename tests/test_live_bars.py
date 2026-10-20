"""Tick-built candles and their overlay on the provider's bars."""
from __future__ import annotations

import time

import pandas as pd

from market.bars import BarBuilder, merge_live


def test_builder_makes_15m_and_1h_bars():
    b = BarBuilder()
    t0 = 1_800_000_000 - (1_800_000_000 % 3600)          # an exact hour
    for i, px in enumerate([100.0, 101.0, 99.5, 100.5]):
        b.on_tick(px, t0 + i * 60)
    b.on_tick(102.0, t0 + 16 * 60)                        # second 15-minute bar
    m15, h1 = b.frame(900), b.frame(3600)
    assert len(m15) == 2 and len(h1) == 1
    first = m15.iloc[0]
    assert first["open"] == 100.0 and first["high"] == 101.0 and first["low"] == 99.5 and first["close"] == 100.5 and first["ticks"] == 4
    assert h1.iloc[0]["high"] == 102.0 and h1.iloc[0]["ticks"] == 5
    st = b.status()
    assert st["bars_15m"] == 2 and st["bars_1h"] == 1


def test_merge_replaces_complete_bars_and_extends_the_forming_one():
    t0 = 1_800_000_000 - (1_800_000_000 % 3600)
    idx = pd.to_datetime([t0, t0 + 900, t0 + 1800], unit="s", utc=True)
    provider = pd.DataFrame({"open": [10, 11, 12.0], "high": [10.5, 11.5, 12.2], "low": [9.5, 10.5, 11.8],
                             "close": [10.2, 11.2, 12.0], "volume": [1.0, 1.0, 1.0]}, index=idx)
    live = pd.DataFrame({"open": [11.1, 12.0, 12.3], "high": [11.9, 12.9, 12.6], "low": [10.9, 11.7, 12.1],
                         "close": [11.4, 12.4, 12.5], "volume": [0, 0, 0.0], "ticks": [40, 5, 3]},
                        index=pd.to_datetime([t0 + 900, t0 + 1800, t0 + 2700], unit="s", utc=True))
    now = t0 + 2700 + 30                                  # the third live bar is forming
    out = merge_live(provider, live, 900, now=now)
    assert out.at[idx[1], "high"] == 11.9 and out.at[idx[1], "close"] == 11.4      # complete + enough ticks → replaced
    # bar 2 is complete but has only 5 ticks → provider kept; it is NOT the forming bar either
    assert out.at[idx[2], "close"] == 12.0 and out.at[idx[2], "high"] == 12.2
    assert len(out) == 4 and out.index[-1].timestamp() == t0 + 2700                 # forming bar appended
    assert out.iloc[-1]["close"] == 12.5
    # a forming bar that the provider already has is extended, not replaced
    provider2 = pd.concat([provider, pd.DataFrame({"open": [12.3], "high": [12.4], "low": [12.2], "close": [12.35], "volume": [0.0]},
                                                  index=pd.to_datetime([t0 + 2700], unit="s", utc=True))])
    out2 = merge_live(provider2, live, 900, now=now)
    last = out2.iloc[-1]
    assert last["high"] == 12.6 and last["low"] == 12.1 and last["close"] == 12.5 and last["open"] == 12.3
    assert merge_live(provider, live.iloc[0:0], 900, now=now) is provider
