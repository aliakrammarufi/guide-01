"""
Signal tracker: turns the analysis bias into explicit BUY / SELL calls with a
stop-loss and two take-profit levels, then follows the live price to report
TP1 hit, TP2 hit, stopped out, or closed because the bias flipped.

Rules (all configurable in Settings):
* A call opens when the bias is BUY/STRONG_BUY (or SELL/STRONG_SELL) with
  confidence >= SIGNAL_MIN_CONF and no call is currently open.
* Entry = price at the time of the call. SL / TP1 / TP2 come from the signal
  engine (ATR-based, tucked behind structure).
* When TP1 is hit the stop moves to break-even (SIGNAL_BREAKEVEN_AFTER_TP1).
* The call closes on SL, TP2, an opposite bias, or after SIGNAL_MAX_HOURS.
* Results are measured in R (multiples of the initial risk).

Every state change is recorded as an event; the Telegram bot and the
terminal both read those events to notify you.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from news.store import Store

log = logging.getLogger(__name__)

OPEN_STATUSES = {"active", "tp1"}


@dataclass
class TradeSignal:
    id: int
    side: str                 # BUY | SELL
    entry: float
    sl: float
    tp1: float
    tp2: float
    confidence: int
    opened_at: datetime
    status: str = "active"    # active | tp1 | tp2 | stopped | flipped | expired
    closed_at: datetime | None = None
    exit_price: float | None = None
    result_r: float | None = None
    tp1_hit_at: datetime | None = None
    reason: str = ""
    sl_current: float | None = None   # after break-even move

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl) or 1e-9

    def r_at(self, price: float) -> float:
        return (price - self.entry) * self.sign / self.risk

    @property
    def effective_sl(self) -> float:
        return self.sl_current if self.sl_current is not None else self.sl

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "side": self.side, "entry": round(self.entry, 2), "sl": round(self.sl, 2),
            "sl_current": round(self.effective_sl, 2), "tp1": round(self.tp1, 2), "tp2": round(self.tp2, 2),
            "confidence": self.confidence, "opened_at": self.opened_at.isoformat(), "status": self.status,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "exit_price": round(self.exit_price, 2) if self.exit_price is not None else None,
            "result_r": round(self.result_r, 2) if self.result_r is not None else None,
            "tp1_hit_at": self.tp1_hit_at.isoformat() if self.tp1_hit_at else None,
            "reason": self.reason, "risk": round(self.risk, 2),
            "rr": round(abs(self.tp2 - self.entry) / self.risk, 2),
        }


@dataclass
class SignalEvent:
    kind: str                 # opened | tp1 | tp2 | stopped | flipped | expired
    signal: TradeSignal
    price: float
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def text(self) -> str:
        s = self.signal
        if self.kind == "opened":
            return f"{s.side} XAU/USD @ {s.entry:,.1f} · SL {s.sl:,.1f} · TP1 {s.tp1:,.1f} · TP2 {s.tp2:,.1f} · conf {s.confidence}%"
        if self.kind == "tp1":
            return f"TP1 hit @ {self.price:,.1f} ({s.r_at(self.price):+.2f}R) · stop moved to break-even {s.effective_sl:,.1f}"
        if self.kind == "tp2":
            return f"TP2 hit @ {self.price:,.1f} · closed {s.result_r:+.2f}R"
        if self.kind == "stopped":
            return f"Stopped out @ {self.price:,.1f} · closed {s.result_r:+.2f}R"
        if self.kind == "flipped":
            return f"Bias flipped · closed @ {self.price:,.1f} ({s.result_r:+.2f}R)"
        if self.kind == "expired":
            return f"Expired after max hold · closed @ {self.price:,.1f} ({s.result_r:+.2f}R)"
        return self.kind

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "signal_id": self.signal.id, "side": self.signal.side, "price": round(self.price, 2),
                "at": self.at.isoformat(), "text": self.text}


class SignalTracker:
    def __init__(self, store: Store, min_conf: int = 45, max_hours: float = 48.0, breakeven_after_tp1: bool = True):
        self.store = store
        self.min_conf = min_conf
        self.max_hours = max_hours
        self.breakeven_after_tp1 = breakeven_after_tp1
        self._pending: list[SignalEvent] = []

    # ---- reads --------------------------------------------------------------
    def active(self) -> TradeSignal | None:
        return self.store.active_signal()

    def history(self, limit: int = 20) -> list[TradeSignal]:
        return self.store.signals(limit=limit)

    def events(self, limit: int = 30) -> list[dict]:
        return self.store.signal_events(limit=limit)

    def drain_events(self) -> list[SignalEvent]:
        out, self._pending = self._pending, []
        return out

    def stats(self) -> dict[str, Any]:
        closed = [s for s in self.store.signals(limit=500) if not s.is_open and s.result_r is not None]
        wins = [s for s in closed if s.result_r > 0.05]
        losses = [s for s in closed if s.result_r < -0.05]
        total_r = sum(s.result_r for s in closed)
        return {
            "count": len(closed), "wins": len(wins), "losses": len(losses),
            "breakeven": len(closed) - len(wins) - len(losses),
            "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else None,
            "total_r": round(total_r, 2), "avg_r": round(total_r / len(closed), 2) if closed else None,
        }

    # ---- writes -------------------------------------------------------------
    def _emit(self, kind: str, sig: TradeSignal, price: float) -> SignalEvent:
        ev = SignalEvent(kind, sig, price)
        self.store.add_signal_event(sig.id, kind, price, ev.text)
        self._pending.append(ev)
        log.info("Signal %s #%d: %s", kind, sig.id, ev.text)
        return ev

    def _close(self, sig: TradeSignal, status: str, price: float, now: datetime) -> SignalEvent:
        sig.status = status
        sig.closed_at = now
        sig.exit_price = price
        sig.result_r = sig.r_at(price)
        self.store.update_signal(sig)
        return self._emit(status, sig, price)

    def on_analysis(self, report, now: datetime | None = None) -> list[SignalEvent]:
        """Called after every analysis: may open a call or close one on a bias flip."""
        now = now or datetime.now(timezone.utc)
        sig_engine = report.signal
        price = report.tech.price
        events: list[SignalEvent] = []
        active = self.active()
        direction = sig_engine.direction  # long | short | flat

        if active is not None:
            # opposite bias → close at market
            if (active.side == "BUY" and direction == "short") or (active.side == "SELL" and direction == "long"):
                events.append(self._close(active, "flipped", price, now))
                active = None
            else:
                events.extend(self.on_price(price, now))
                active = self.active()

        if active is None and direction in ("long", "short") and sig_engine.confidence >= self.min_conf \
                and sig_engine.stop is not None and sig_engine.target1 is not None and sig_engine.target2 is not None:
            side = "BUY" if direction == "long" else "SELL"
            reason = "; ".join(report.tech.reasons[:2]) + (f" · news {sig_engine.news_score:+d}" if abs(sig_engine.news_score) >= 15 else "")
            sig = TradeSignal(
                id=0, side=side, entry=price, sl=sig_engine.stop, tp1=sig_engine.target1, tp2=sig_engine.target2,
                confidence=sig_engine.confidence, opened_at=now, reason=reason[:200],
            )
            sig.id = self.store.insert_signal(sig)
            events.append(self._emit("opened", sig, price))
        return events

    def on_price(self, price: float, now: datetime | None = None) -> list[SignalEvent]:
        """Called on every quote: checks SL / TP1 / TP2 / expiry for the open call."""
        now = now or datetime.now(timezone.utc)
        sig = self.active()
        if sig is None or price is None:
            return []
        events: list[SignalEvent] = []
        hit = lambda level, above: (price >= level) if above else (price <= level)  # noqa: E731
        long = sig.side == "BUY"

        if hit(sig.effective_sl, above=not long):
            events.append(self._close(sig, "stopped", price, now))
            return events
        if hit(sig.tp2, above=long):
            events.append(self._close(sig, "tp2", price, now))
            return events
        if sig.status == "active" and hit(sig.tp1, above=long):
            sig.status = "tp1"
            sig.tp1_hit_at = now
            if self.breakeven_after_tp1:
                sig.sl_current = sig.entry
            self.store.update_signal(sig)
            events.append(self._emit("tp1", sig, price))
        if now - sig.opened_at > timedelta(hours=self.max_hours):
            events.append(self._close(sig, "expired", price, now))
        return events

    # ---- demo ---------------------------------------------------------------
    def seed_demo(self) -> None:
        """A few finished calls so the history and stats have something to show."""
        if self.store.signals(limit=1):
            return
        now = datetime.now(timezone.utc)
        rows = [
            ("BUY", 4441.0, 4418.0, 4462.0, 4483.0, 64, 3.2, "tp2", 4483.4),
            ("SELL", 4502.0, 4524.0, 4482.0, 4461.0, 58, 2.5, "stopped", 4524.2),
            ("BUY", 4468.5, 4446.0, 4489.0, 4510.0, 61, 1.8, "tp1", None),   # closed at break-even after TP1
            ("BUY", 4489.0, 4466.0, 4511.0, 4533.0, 66, 0.9, "tp2", 4533.1),
        ]
        for side, entry, sl, tp1, tp2, conf, days_ago, status, exit_price in rows:
            opened = now - timedelta(days=days_ago)
            sig = TradeSignal(id=0, side=side, entry=entry, sl=sl, tp1=tp1, tp2=tp2, confidence=conf, opened_at=opened, reason="demo")
            sig.id = self.store.insert_signal(sig)
            if status == "tp1":
                sig.tp1_hit_at = opened + timedelta(hours=5)
                sig.sl_current = entry
                sig.status = "flipped"
                sig.closed_at = opened + timedelta(hours=14)
                sig.exit_price = entry + 0.4
            else:
                sig.status = status
                sig.closed_at = opened + timedelta(hours=9)
                sig.exit_price = exit_price
            sig.result_r = sig.r_at(sig.exit_price)
            self.store.update_signal(sig)
            self.store.add_signal_event(sig.id, "opened", entry, f"{side} XAU/USD @ {entry:,.1f}", at=opened)
            self.store.add_signal_event(sig.id, sig.status, sig.exit_price, f"{sig.status} @ {sig.exit_price:,.1f} ({sig.result_r:+.2f}R)", at=sig.closed_at)
