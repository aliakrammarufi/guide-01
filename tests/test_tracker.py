from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from news.store import Store
from signals.tracker import SignalTracker


def _sig(direction, confidence, price, stop, t1, t2):
    long = direction == "long"
    zone_lo, zone_hi = (price - 12, price - 4) if long else (price + 4, price + 12)
    chase = price + 10 if long else price - 10
    return SimpleNamespace(direction=direction, confidence=confidence, stop=stop, target1=t1, target2=t2, news_score=30,
                           zone_lo=zone_lo, zone_hi=zone_hi, chase_limit=chase, zone_mid=(zone_lo + zone_hi) / 2,
                           entry_mode="pullback", atr=15.0)


def _report(direction="long", confidence=60, price=4500.0, stop=4475.0, t1=4520.0, t2=4545.0, m15=None, session=None):
    tech = SimpleNamespace(price=price, reasons=["EMA stack bullish", "RSI 57"], atr=15.0)
    return SimpleNamespace(signal=_sig(direction, confidence, price, stop, t1, t2), tech=tech,
                           data=SimpleNamespace(m15=m15), session=session)


def _m15(closes, end):
    idx = pd.date_range(end=end, periods=len(closes), freq="15min")
    c = np.array(closes, float); o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3, "close": c, "volume": 1.0}, index=idx)


# ---------------------------------------------------------------- market-entry mode (trigger disabled)
def test_market_mode_open_tp1_breakeven_then_tp2(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45, require_trigger=False)
    ev = tr.on_analysis(_report())
    assert [e.kind for e in ev] == ["setup", "triggered"]
    a = tr.active()
    assert a.side == "BUY" and a.entry == 4500.0 and a.sl == 4475.0 and a.risk == 25.0 and a.is_open
    assert tr.on_price(4510.0) == []
    assert [e.kind for e in tr.on_price(4521.0)] == ["tp1"]
    a = tr.active(); assert a.status == "tp1" and a.effective_sl == 4500.0
    assert [e.kind for e in tr.on_price(4546.0)] == ["tp2"]
    assert tr.active() is None
    h = tr.history()[0]
    assert h.status == "tp2" and abs(h.result_r - (46.0 / 25.0)) < 1e-6
    st = tr.stats(); assert st["count"] == 1 and st["wins"] == 1 and st["win_rate"] == 100.0


def test_market_mode_stop_out_and_no_duplicate(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45, require_trigger=False)
    tr.on_analysis(_report())
    assert tr.on_analysis(_report()) == []
    assert [e.kind for e in tr.on_price(4474.0)] == ["stopped"]
    assert tr.history()[0].result_r == -1.04


def test_market_mode_sell_and_flip(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45, require_trigger=False)
    tr.on_analysis(_report(direction="short", price=4500.0, stop=4525.0, t1=4480.0, t2=4455.0))
    assert tr.active().side == "SELL"
    ev = tr.on_analysis(_report(direction="long", price=4490.0))
    assert [e.kind for e in ev] == ["flipped", "setup", "triggered"]
    assert tr.history()[1].status == "flipped" and tr.active().side == "BUY"


def test_confidence_gate_session_gate_and_expiry(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45, max_hours=2, require_trigger=False)
    assert tr.on_analysis(_report(confidence=30)) == []
    closed = SimpleNamespace(allow_entries=False)
    assert tr.on_analysis(_report(session=closed)) == []            # dead zone / weekend / news blackout
    t0 = datetime.now(timezone.utc) - timedelta(hours=3)
    tr.on_analysis(_report(), now=t0)
    assert [e.kind for e in tr.on_price(4505.0)] == ["expired"]
    assert tr.history()[0].result_r == 0.2


# ---------------------------------------------------------------- pending setups with the 15-minute trigger
def test_pending_waits_then_triggers_on_pullback(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45)
    now = datetime(2026, 10, 19, 12, 10, tzinfo=timezone.utc)
    # price well above the zone (4488-4496): setup only
    ev = tr.on_analysis(_report(price=4500.0, m15=_m15([4505 - i * 0.2 for i in range(30)], now)), now=now)
    assert [e.kind for e in ev] == ["setup"]
    p = tr.pending(); assert p is not None and p.status == "pending" and tr.active() is None and not p.touched
    assert tr.live().is_pending
    # price dips into the zone and prints a bullish engulfing bar -> triggered at the bar close
    closes = [4505 - i * 0.4 for i in range(30)] + [4493.0, 4491.0, 4490.5, 4489.8, 4494.5, 4496.0]
    df = _m15(closes, now)
    df.loc[df.index[-2], ["open", "close", "low", "high"]] = [4489.9, 4494.5, 4489.0, 4494.8]
    df.loc[df.index[-3], ["open", "close", "low", "high"]] = [4490.6, 4489.8, 4489.5, 4490.8]
    ev = tr.on_analysis(_report(price=4494.5, m15=df), now=now)      # 12:00 bar still forming → engulfing bar is the last closed
    assert [e.kind for e in ev] == ["triggered"]
    a = tr.active(); assert a.entry == 4494.5 and a.status == "active" and a.trigger_note == "15m engulfing candle at the zone"
    assert tr.pending() is None


def test_pending_missed_invalidated_cancelled(tmp_path):
    now = datetime(2026, 10, 19, 12, 10, tzinfo=timezone.utc)
    quiet = _m15([4505 - i * 0.2 for i in range(30)], now)
    # missed: price runs past the chase level without a pullback
    tr = SignalTracker(Store(tmp_path / "a.db"), min_conf=45)
    tr.on_analysis(_report(price=4500.0, m15=quiet), now=now)
    assert [e.kind for e in tr.on_price(4511.0, now)] == ["missed"]
    assert tr.pending() is None and tr.history()[0].result_r is None and tr.stats()["no_trade"] == 1
    # invalidated: price trades through the stop before any confirmation
    tr = SignalTracker(Store(tmp_path / "b.db"), min_conf=45)
    tr.on_analysis(_report(price=4500.0, m15=quiet), now=now)
    assert [e.kind for e in tr.on_price(4474.0, now)] == ["invalidated"]
    # cancelled: bias flips while pending, and the opposite setup is published
    tr = SignalTracker(Store(tmp_path / "c.db"), min_conf=45)
    tr.on_analysis(_report(price=4500.0, m15=quiet), now=now)
    ev = tr.on_analysis(_report(direction="short", price=4500.0, stop=4525.0, t1=4480.0, t2=4455.0, m15=quiet), now=now)
    assert [e.kind for e in ev] == ["cancelled", "setup"] and tr.pending().side == "SELL"
    # stale: waited too long
    tr = SignalTracker(Store(tmp_path / "d.db"), min_conf=45, pending_max_hours=1)
    tr.on_analysis(_report(price=4500.0, m15=quiet), now=now)
    ev = tr.on_analysis(_report(price=4500.0, m15=quiet), now=now + timedelta(hours=2))
    assert [e.kind for e in ev] == ["stale"]


def test_zone_follows_analysis_until_touched(tmp_path):
    now = datetime(2026, 10, 19, 12, 10, tzinfo=timezone.utc)
    quiet = _m15([4505 - i * 0.2 for i in range(30)], now)
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45)
    tr.on_analysis(_report(price=4500.0, m15=quiet), now=now)
    assert tr.pending().zone_hi == 4496.0
    tr.on_analysis(_report(price=4504.0, m15=quiet), now=now)      # EMAs drifted up: zone moves with them
    assert tr.pending().zone_hi == 4500.0
    tr.on_price(4498.0, now)                                        # price enters the zone: it is now fixed
    tr.on_analysis(_report(price=4508.0, m15=quiet), now=now)
    assert tr.pending().zone_hi == 4500.0


def test_events_persist_and_drain(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45, require_trigger=False)
    tr.on_analysis(_report())
    tr.on_price(4521.0)
    kinds = [e.kind for e in tr.drain_events()]
    assert kinds == ["setup", "triggered", "tp1"] and tr.drain_events() == []
    stored = tr.events()
    assert [e["kind"] for e in stored] == ["tp1", "triggered", "setup"] and "break-even" in stored[0]["text"]
