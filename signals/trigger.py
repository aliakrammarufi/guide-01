"""
15-minute entry confirmation.

A BUY/SELL setup only becomes a live call once price has pulled back into the
entry zone AND the 15-minute chart confirms the move is resuming.  Four
confirmations are recognised (long side described, shorts are mirrored):

* ``ema_reclaim``   the last closed M15 bar closes back above the M15 EMA20 after
                    trading below it, and the bar is bullish
* ``engulfing``     a bullish bar whose body engulfs the previous bar's body, with its
                    low inside the zone
* ``pin_bar``       a long lower wick (≥ 2× body) rejecting the zone, close in the top
                    40% of the bar
* ``break_high``    the last closed bar closes above the highest high of the previous
                    four bars after the zone was touched (micro structure break)

Only *closed* bars count: yfinance ships the in-progress bar too, and it is ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from market import indicators as ind

TOUCH_LOOKBACK = 10      # bars in which the zone must have been touched
TOUCH_TOLERANCE = 0.2    # × ATR – how far beyond the zone edge still counts as a touch


@dataclass
class TriggerResult:
    fired: bool
    kind: str = ""
    note: str = ""
    touched: bool = False          # price has visited the zone recently
    bar_time: datetime | None = None
    entry: float | None = None     # close of the confirming bar
    bars_checked: int = 0

    def to_dict(self) -> dict:
        return {"fired": self.fired, "kind": self.kind, "note": self.note, "touched": self.touched,
                "bar_time": self.bar_time.isoformat() if self.bar_time else None, "entry": self.entry}


KIND_LABELS = {
    "ema_reclaim": "15m close back across EMA20",
    "engulfing": "15m engulfing candle at the zone",
    "pin_bar": "15m rejection wick at the zone",
    "break_high": "15m break of the pullback high",
    "break_low": "15m break of the pullback low",
}


def closed_bars(m15: pd.DataFrame, now: datetime | None = None, bar_minutes: int = 15) -> pd.DataFrame:
    """Drop the in-progress bar (its timestamp + 15 min is still in the future)."""
    if m15 is None or m15.empty:
        return m15
    now = now or datetime.now(timezone.utc)
    last = m15.index[-1].to_pydatetime()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last + timedelta(minutes=bar_minutes) > now:
        return m15.iloc[:-1]
    return m15


def evaluate(side: str, zone_lo: float, zone_hi: float, atr: float, m15: pd.DataFrame | None,
             now: datetime | None = None) -> TriggerResult:
    """Decide whether the 15-minute chart confirms an entry for ``side`` at the zone."""
    if m15 is None or len(m15) < 25:
        return TriggerResult(False, note="no 15-minute data")
    df = closed_bars(m15, now)
    if len(df) < 25:
        return TriggerResult(False, note="not enough closed 15-minute bars")
    tol = TOUCH_TOLERANCE * max(atr, 1e-6)
    recent = df.tail(TOUCH_LOOKBACK)
    long = side == "BUY"

    # 1) has price visited the zone recently?
    if long:
        touched = bool(((recent["low"] <= zone_hi + tol) & (recent["low"] >= zone_lo - tol * 2.5)).any()) or \
            bool(((recent["low"] <= zone_hi) & (recent["high"] >= zone_lo)).any())
    else:
        touched = bool(((recent["high"] >= zone_lo - tol) & (recent["high"] <= zone_hi + tol * 2.5)).any()) or \
            bool(((recent["high"] >= zone_lo) & (recent["low"] <= zone_hi)).any())
    res = TriggerResult(False, touched=touched, bars_checked=len(recent))
    if not touched:
        res.note = "waiting for price to reach the zone"
        return res

    ema20 = ind.ema(df["close"], 20)
    last, prev = df.iloc[-1], df.iloc[-2]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    bar_time = df.index[-1].to_pydatetime()
    if bar_time.tzinfo is None:
        bar_time = bar_time.replace(tzinfo=timezone.utc)

    def fire(kind: str) -> TriggerResult:
        return TriggerResult(True, kind, KIND_LABELS[kind], touched=True, bar_time=bar_time, entry=c, bars_checked=len(recent))

    if long:
        bullish = c > o
        in_zone = zone_lo - tol <= l <= zone_hi + tol
        # ema reclaim: closed below EMA20 within the last 6 bars, now closed above, bullish bar
        below_recently = bool((df["close"].iloc[-7:-1] < ema20.iloc[-7:-1]).any())
        if bullish and c > float(ema20.iloc[-1]) and below_recently and c > float(prev["close"]):
            return fire("ema_reclaim")
        prev_body = abs(float(prev["close"]) - float(prev["open"]))
        if bullish and in_zone and float(prev["close"]) < float(prev["open"]) and c > float(prev["open"]) \
                and o <= float(prev["close"]) + tol / 4 and body > prev_body:
            return fire("engulfing")
        lower_wick = min(o, c) - l
        if in_zone and lower_wick >= 2 * body and (c - l) / rng >= 0.6:
            return fire("pin_bar")
        pull_high = float(df["high"].iloc[-5:-1].max())
        if bullish and c > pull_high and l >= zone_lo - tol * 2:
            return fire("break_high")
        res.note = "in the zone – no 15-minute confirmation yet"
    else:
        bearish = c < o
        in_zone = zone_lo - tol <= h <= zone_hi + tol
        above_recently = bool((df["close"].iloc[-7:-1] > ema20.iloc[-7:-1]).any())
        if bearish and c < float(ema20.iloc[-1]) and above_recently and c < float(prev["close"]):
            return fire("ema_reclaim")
        prev_body = abs(float(prev["close"]) - float(prev["open"]))
        if bearish and in_zone and float(prev["close"]) > float(prev["open"]) and c < float(prev["open"]) \
                and o >= float(prev["close"]) - tol / 4 and body > prev_body:
            return fire("engulfing")
        upper_wick = h - max(o, c)
        if in_zone and upper_wick >= 2 * body and (h - c) / rng >= 0.6:
            return fire("pin_bar")
        pull_low = float(df["low"].iloc[-5:-1].min())
        if bearish and c < pull_low and h <= zone_hi + tol * 2:
            return fire("break_low")
        res.note = "in the zone – no 15-minute confirmation yet"
    return res
