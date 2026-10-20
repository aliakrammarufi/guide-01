"""
Live candles built from the tick stream.

Yahoo's 15-minute and hourly bars arrive a minute or more late and the last one
is always in progress, so an analysis that only used them would notice a
15-minute confirmation well after the bar closed. ``BarBuilder`` listens to the
tick engine and keeps its own 15-minute and 1-hour OHLC bars (spot basis, same
as the live price) for the last two days. ``merge_live`` lays them over the
provider's bars: complete live bars with enough ticks replace the provider's,
the forming bar is appended so the read is current to the second.

Only ticks that actually arrived are used – nothing is interpolated. A bar with
too few ticks (the stream was down) is never trusted over the provider's bar.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger(__name__)

MIN_TICKS_15M = 30       # a 15-minute bar needs at least this many ticks before it may replace the provider's bar
MIN_TICKS_1H = 100
KEEP_SEC = 2 * 86400


@dataclass
class Bar:
    start: int            # epoch seconds of the bar start
    open: float
    high: float
    low: float
    close: float
    ticks: int = 0

    def update(self, price: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.ticks += 1


class BarBuilder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bars: dict[int, dict[int, Bar]] = {900: {}, 3600: {}}
        self.last_tick_at: float = 0.0
        self.started_at = time.time()

    def on_tick(self, price: float, ts: float | None = None) -> None:
        if not price or price <= 0:
            return
        t = float(ts or time.time())
        with self._lock:
            self.last_tick_at = t
            for span, bars in self._bars.items():
                start = int(t // span) * span
                bar = bars.get(start)
                if bar is None:
                    bars[start] = Bar(start, price, price, price, price, 1)
                    for old in [k for k in bars if k < start - KEEP_SEC]:
                        del bars[old]
                else:
                    bar.update(price)

    def frame(self, span: int) -> pd.DataFrame:
        """OHLC bars for ``span`` seconds (900 or 3600) as a DataFrame with a UTC index (oldest first)."""
        with self._lock:
            rows = sorted(self._bars[span].values(), key=lambda b: b.start)
            data = [(b.start, b.open, b.high, b.low, b.close, 0.0, b.ticks) for b in rows]
        if not data:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "ticks"], dtype=float)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume", "ticks"])
        df.index = pd.to_datetime(df.pop("ts"), unit="s", utc=True)
        return df

    def status(self) -> dict:
        with self._lock:
            return {"bars_15m": len(self._bars[900]), "bars_1h": len(self._bars[3600]),
                    "last_tick_age_sec": round(time.time() - self.last_tick_at, 1) if self.last_tick_at else None}


LIVE: BarBuilder | None = None     # set by main.py when the tick engine runs


def merge_live(provider: pd.DataFrame | None, live: pd.DataFrame, span: int, now: float | None = None) -> pd.DataFrame | None:
    """Overlay live bars on the provider's. Complete live bars with enough ticks replace the provider's bar
    for the same start; newer bars (including the forming one) are appended. Older live bars that the
    provider does not have are appended too. The provider's older history is kept untouched."""
    if provider is None or live is None or live.empty:
        return provider
    now = now or time.time()
    min_ticks = MIN_TICKS_15M if span == 900 else MIN_TICKS_1H
    out = provider.copy()
    out_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in out.columns]
    last_prov = out.index[-1] if len(out) else None
    replaced = appended = 0
    for ts, row in live.iterrows():
        complete = (ts.timestamp() + span) <= now
        enough = int(row["ticks"]) >= min_ticks
        vals = {"open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"])}
        if ts in out.index:
            if complete and enough:
                for c in ("open", "high", "low", "close"):
                    out.at[ts, c] = vals[c]
                replaced += 1
            elif not complete and ts == last_prov:
                # the forming bar: the provider's copy is minutes old – extend it with what ticked since
                out.at[ts, "high"] = max(float(out.at[ts, "high"]), vals["high"])
                out.at[ts, "low"] = min(float(out.at[ts, "low"]), vals["low"])
                out.at[ts, "close"] = vals["close"]
                replaced += 1
        elif last_prov is None or ts > last_prov or enough:
            new = {c: vals.get(c, 0.0) for c in out_cols}
            if "volume" in out_cols:
                new["volume"] = 0.0
            out.loc[ts] = new
            appended += 1
    if appended:
        out = out.sort_index()
    if replaced or appended:
        log.debug("live bars %ds: replaced %d, appended %d", span, replaced, appended)
    return out
