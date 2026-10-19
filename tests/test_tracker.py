from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from news.store import Store
from signals.tracker import SignalTracker


def _report(direction="long", confidence=60, price=4500.0, stop=4475.0, t1=4520.0, t2=4545.0):
    sig = SimpleNamespace(direction=direction, confidence=confidence, stop=stop, target1=t1, target2=t2, news_score=30)
    tech = SimpleNamespace(price=price, reasons=["EMA stack bullish", "RSI 57"])
    return SimpleNamespace(signal=sig, tech=tech)


def test_open_tp1_breakeven_then_tp2(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45)
    ev = tr.on_analysis(_report())
    assert [e.kind for e in ev] == ["opened"]
    a = tr.active()
    assert a.side == "BUY" and a.entry == 4500.0 and a.sl == 4475.0 and a.risk == 25.0

    assert tr.on_price(4510.0) == []                       # nothing yet
    ev = tr.on_price(4521.0)                               # TP1
    assert [e.kind for e in ev] == ["tp1"]
    a = tr.active(); assert a.status == "tp1" and a.effective_sl == 4500.0   # break-even

    ev = tr.on_price(4546.0)                               # TP2 closes
    assert [e.kind for e in ev] == ["tp2"]
    assert tr.active() is None
    h = tr.history()[0]
    assert h.status == "tp2" and abs(h.result_r - (46.0 / 25.0)) < 1e-6
    st = tr.stats(); assert st["count"] == 1 and st["wins"] == 1 and st["win_rate"] == 100.0


def test_stop_out_and_no_duplicate_open(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45)
    tr.on_analysis(_report())
    assert tr.on_analysis(_report()) == []                 # already open → no second call
    ev = tr.on_price(4474.0)
    assert [e.kind for e in ev] == ["stopped"]
    assert tr.history()[0].result_r == -1.04


def test_sell_side_and_flip(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45)
    tr.on_analysis(_report(direction="short", price=4500.0, stop=4525.0, t1=4480.0, t2=4455.0))
    a = tr.active(); assert a.side == "SELL"
    assert tr.on_price(4495.0) == [] and tr.active().r_at(4495.0) == 0.2
    ev = tr.on_analysis(_report(direction="long", price=4490.0))   # bias flips → close, then a new BUY opens
    assert [e.kind for e in ev] == ["flipped", "opened"]
    assert tr.history()[1].status == "flipped" and tr.active().side == "BUY"


def test_confidence_gate_and_expiry(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"), min_conf=45, max_hours=2)
    assert tr.on_analysis(_report(confidence=30)) == []
    t0 = datetime.now(timezone.utc) - timedelta(hours=3)
    tr.on_analysis(_report(), now=t0)
    ev = tr.on_price(4505.0)
    assert [e.kind for e in ev] == ["expired"]
    assert tr.history()[0].result_r == 0.2


def test_events_persist_and_drain(tmp_path):
    tr = SignalTracker(Store(tmp_path / "s.db"))
    tr.on_analysis(_report())
    assert len(tr.drain_events()) == 1 and tr.drain_events() == []
    assert tr.events()[0]["kind"] == "opened"
