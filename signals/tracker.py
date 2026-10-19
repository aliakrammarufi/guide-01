"""
Signal tracker: turns the analysis bias into explicit BUY / SELL calls and
follows them through their life.

Life of a call
--------------
1. **pending** – the bias qualifies (confidence ≥ SIGNAL_MIN_CONF, session allows
   entries). The call is published as a *setup*: an entry zone to wait for, a
   stop beyond it, two targets and a "don't chase beyond" level. Nothing is
   "entered" yet.
2. **active** – price pulled back into the zone and the 15-minute chart confirmed
   (EMA reclaim, engulfing, rejection wick or micro structure break). The entry is
   the close of the confirming bar. From here the old rules apply:
3. **tp1** – stop moves to break-even;  **tp2 / stopped / flipped / expired** – closed,
   result measured in R (multiples of the initial risk).

A pending setup ends without a trade when price runs past the chase level
(**missed**), trades through the stop before confirming (**invalidated**), the
bias flips or confidence collapses (**cancelled**), or it waits too long
(**stale**).  While price has not yet touched the zone, the zone follows the
latest analysis (the EMAs drift), so the setup stays realistic.

Every state change is recorded as an event; the Telegram bot and the
terminal read those events to notify you.  The bot never places orders.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from news.store import Store
from signals import trigger as trig

log = logging.getLogger(__name__)

OPEN_STATUSES = {"active", "tp1"}
PENDING_STATUSES = {"pending"}
NO_TRADE_STATUSES = {"missed", "invalidated", "cancelled", "stale"}


@dataclass
class TradeSignal:
    id: int
    side: str                 # BUY | SELL
    entry: float              # planned entry (zone middle) while pending; actual fill once active
    sl: float
    tp1: float
    tp2: float
    confidence: int
    opened_at: datetime       # when the setup was published
    status: str = "pending"   # pending | active | tp1 | tp2 | stopped | flipped | expired | missed | invalidated | cancelled | stale
    closed_at: datetime | None = None
    exit_price: float | None = None
    result_r: float | None = None
    tp1_hit_at: datetime | None = None
    reason: str = ""
    sl_current: float | None = None   # after break-even move
    zone_lo: float | None = None
    zone_hi: float | None = None
    chase: float | None = None        # don't chase beyond this
    triggered_at: datetime | None = None
    trigger_note: str = ""
    setup_mode: str = ""              # pullback | in-zone | market (legacy)
    touched: bool = False             # price has visited the zone (persisted)

    # ---- state ---------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_pending(self) -> bool:
        return self.status in PENDING_STATUSES

    @property
    def is_live(self) -> bool:
        return self.is_open or self.is_pending

    @property
    def traded(self) -> bool:
        return self.status not in PENDING_STATUSES | NO_TRADE_STATUSES

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

    @property
    def rr(self) -> float:
        return abs(self.tp2 - self.entry) / self.risk

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "side": self.side, "entry": round(self.entry, 2), "sl": round(self.sl, 2),
            "sl_current": round(self.effective_sl, 2), "tp1": round(self.tp1, 2), "tp2": round(self.tp2, 2),
            "confidence": self.confidence, "opened_at": self.opened_at.isoformat(), "status": self.status,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "exit_price": round(self.exit_price, 2) if self.exit_price is not None else None,
            "result_r": round(self.result_r, 2) if self.result_r is not None else None,
            "tp1_hit_at": self.tp1_hit_at.isoformat() if self.tp1_hit_at else None,
            "reason": self.reason, "risk": round(self.risk, 2), "rr": round(self.rr, 2),
            "zone_lo": self.zone_lo, "zone_hi": self.zone_hi, "chase": self.chase,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "trigger_note": self.trigger_note, "setup_mode": self.setup_mode,
            "is_open": self.is_open, "is_pending": self.is_pending, "touched": self.touched,
        }


@dataclass
class SignalEvent:
    kind: str                 # setup | triggered | tp1 | tp2 | stopped | flipped | expired | missed | invalidated | cancelled | stale
    signal: TradeSignal
    price: float
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def text(self) -> str:
        s = self.signal
        k = self.kind
        if k == "setup":
            if s.setup_mode == "in-zone":
                return (f"{s.side} setup · price is in the zone {s.zone_lo:,.1f}–{s.zone_hi:,.1f} · waiting for a 15-minute confirmation · "
                        f"SL {s.sl:,.1f} · TP1 {s.tp1:,.1f} · TP2 {s.tp2:,.1f} · conf {s.confidence}%")
            return (f"{s.side} setup · wait for a pullback into {s.zone_lo:,.1f}–{s.zone_hi:,.1f} · don't chase beyond {s.chase:,.1f} · "
                    f"SL {s.sl:,.1f} · TP1 {s.tp1:,.1f} · TP2 {s.tp2:,.1f} · conf {s.confidence}%")
        if k == "triggered":
            return f"{s.side} XAU/USD @ {s.entry:,.1f} · {s.trigger_note} · SL {s.sl:,.1f} · TP1 {s.tp1:,.1f} · TP2 {s.tp2:,.1f}"
        if k == "tp1":
            return f"TP1 hit @ {self.price:,.1f} ({s.r_at(self.price):+.2f}R) · stop moved to break-even {s.effective_sl:,.1f}"
        if k == "tp2":
            return f"TP2 hit @ {self.price:,.1f} · closed {s.result_r:+.2f}R"
        if k == "stopped":
            return f"Stopped out @ {self.price:,.1f} · closed {s.result_r:+.2f}R"
        if k == "flipped":
            return f"Bias flipped · closed @ {self.price:,.1f} ({s.result_r:+.2f}R)"
        if k == "expired":
            return f"Expired after max hold · closed @ {self.price:,.1f} ({s.result_r:+.2f}R)"
        if k == "missed":
            return f"Setup missed · price ran to {self.price:,.1f} without a pullback into the zone (no trade)"
        if k == "invalidated":
            return f"Setup invalidated · price traded through {s.sl:,.1f} before confirming (no trade)"
        if k == "cancelled":
            return "Setup cancelled · bias changed before an entry (no trade)"
        if k == "stale":
            return "Setup expired · no pullback and confirmation in time (no trade)"
        return k

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "signal_id": self.signal.id, "side": self.signal.side, "price": round(self.price, 2),
                "at": self.at.isoformat(), "text": self.text}


class SignalTracker:
    def __init__(self, store: Store, min_conf: int = 45, max_hours: float = 48.0, breakeven_after_tp1: bool = True,
                 pending_max_hours: float = 12.0, require_trigger: bool = True):
        self.store = store
        self.min_conf = min_conf
        self.max_hours = max_hours
        self.breakeven_after_tp1 = breakeven_after_tp1
        self.pending_max_hours = pending_max_hours
        self.require_trigger = require_trigger
        self._pending: list[SignalEvent] = []
        self.last_trigger: dict | None = None      # last trigger evaluation (for the UI)
        self._lock = threading.RLock()             # on_price (tick thread) and on_analysis (pipeline thread) never interleave
        self.listeners: list[Callable[[SignalEvent], None]] = []   # notified on every event, immediately
        self.quiet = False                         # True for replays: events are not logged at INFO

    # ---- reads --------------------------------------------------------------
    def active(self) -> TradeSignal | None:
        return self.store.active_signal()

    def pending(self) -> TradeSignal | None:
        return self.store.pending_signal()

    def live(self) -> TradeSignal | None:
        """The call to show: an open trade, else a pending setup."""
        return self.active() or self.pending()

    def history(self, limit: int = 20) -> list[TradeSignal]:
        return self.store.signals(limit=limit)

    def events(self, limit: int = 30) -> list[dict]:
        return self.store.signal_events(limit=limit)

    def drain_events(self) -> list[SignalEvent]:
        out, self._pending = self._pending, []
        return out

    def stats(self) -> dict[str, Any]:
        closed = [s for s in self.store.signals(limit=500) if s.traded and not s.is_open and s.result_r is not None]
        wins = [s for s in closed if s.result_r > 0.05]
        losses = [s for s in closed if s.result_r < -0.05]
        total_r = sum(s.result_r for s in closed)
        no_trade = [s for s in self.store.signals(limit=500) if s.status in NO_TRADE_STATUSES]
        return {
            "count": len(closed), "wins": len(wins), "losses": len(losses),
            "breakeven": len(closed) - len(wins) - len(losses),
            "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else None,
            "total_r": round(total_r, 2), "avg_r": round(total_r / len(closed), 2) if closed else None,
            "no_trade": len(no_trade),
        }

    # ---- writes -------------------------------------------------------------
    def _emit(self, kind: str, sig: TradeSignal, price: float) -> SignalEvent:
        ev = SignalEvent(kind, replace(sig), price)      # snapshot: later state changes must not rewrite this message
        self.store.add_signal_event(sig.id, kind, price, ev.text)
        self._pending.append(ev)
        (log.debug if self.quiet else log.info)("Signal %s #%d: %s", kind, sig.id, ev.text)
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:  # noqa: BLE001
                log.exception("signal listener failed")
        return ev

    def _mark_touched(self, sig: TradeSignal) -> None:
        if not sig.touched:
            sig.touched = True
            self.store.update_signal(sig)

    def _close(self, sig: TradeSignal, status: str, price: float, now: datetime) -> SignalEvent:
        sig.status = status
        sig.closed_at = now
        sig.exit_price = price
        sig.result_r = sig.r_at(price) if status not in NO_TRADE_STATUSES else None
        self.store.update_signal(sig)
        return self._emit(status, sig, price)

    @staticmethod
    def _plan_from_signal(engine_sig) -> dict | None:
        if engine_sig.zone_lo is None or engine_sig.stop is None or engine_sig.target1 is None or engine_sig.target2 is None:
            return None
        return {"zone_lo": engine_sig.zone_lo, "zone_hi": engine_sig.zone_hi, "chase": engine_sig.chase_limit,
                "sl": engine_sig.stop, "tp1": engine_sig.target1, "tp2": engine_sig.target2,
                "entry": engine_sig.zone_mid, "mode": engine_sig.entry_mode or "pullback"}

    def on_analysis(self, report, now: datetime | None = None) -> list[SignalEvent]:
        """Called after every analysis. May publish a setup, trigger it, cancel it, or close an open call."""
        with self._lock:
            return self._on_analysis(report, now)

    def _on_analysis(self, report, now: datetime | None = None) -> list[SignalEvent]:
        now = now or datetime.now(timezone.utc)
        eng = report.signal
        tech = report.tech
        price = tech.price
        direction = eng.direction                    # long | short | flat
        session = getattr(report, "session", None)
        m15 = getattr(getattr(report, "data", None), "m15", None)
        events: list[SignalEvent] = []

        # ---- open trade: flip (then fall through to publish the opposite setup) or follow price
        active = self.active()
        if active is not None:
            if (active.side == "BUY" and direction == "short") or (active.side == "SELL" and direction == "long"):
                events.append(self._close(active, "flipped", price, now))
            else:
                events.extend(self.on_price(price, now))
                return events

        # ---- pending setup: cancel (then maybe publish the opposite), refresh, or trigger
        pend = self.pending()
        if pend is not None:
            opposite = (pend.side == "BUY" and direction == "short") or (pend.side == "SELL" and direction == "long")
            weak = direction == "flat" or eng.confidence < max(0, self.min_conf - 15)
            if opposite or weak:
                events.append(self._close(pend, "cancelled", price, now))
                pend = None
            elif now - pend.opened_at > timedelta(hours=self.pending_max_hours):
                events.append(self._close(pend, "stale", price, now))
                return events
        if pend is not None:
            events.extend(self.on_price(price, now))
            pend = self.pending()
            if pend is None:
                return events
            if session is not None and not session.allow_entries:
                return events            # keep waiting: no new entries during a blackout / dead zone / weekend
            # let the zone follow the analysis until price has visited it
            plan = self._plan_from_signal(eng)
            if plan and not pend.touched and eng.direction == ("long" if pend.side == "BUY" else "short"):
                pend.zone_lo, pend.zone_hi, pend.chase = plan["zone_lo"], plan["zone_hi"], plan["chase"]
                pend.sl, pend.tp1, pend.tp2, pend.entry = plan["sl"], plan["tp1"], plan["tp2"], plan["entry"]
                pend.setup_mode, pend.confidence = plan["mode"], eng.confidence
                self.store.update_signal(pend)
            events.extend(self._try_trigger(pend, tech, m15, now))
            return events

        # ---- no call: publish a setup? -----------------------------------------
        if direction not in ("long", "short") or eng.confidence < self.min_conf:
            return events
        if not getattr(getattr(report, "data", None), "basis_known", True):
            log.warning("Price basis unknown (spot reference down) – not publishing a setup")
            return events
        if session is not None and not session.allow_entries:
            return events
        plan = self._plan_from_signal(eng)
        if plan is None:
            return events
        side = "BUY" if direction == "long" else "SELL"
        reason = "; ".join(tech.reasons[:2]) + (f" · news {eng.news_score:+d}" if abs(eng.news_score) >= 15 else "")
        sig = TradeSignal(
            id=0, side=side, entry=plan["entry"], sl=plan["sl"], tp1=plan["tp1"], tp2=plan["tp2"],
            confidence=eng.confidence, opened_at=now, reason=reason[:200], status="pending",
            zone_lo=plan["zone_lo"], zone_hi=plan["zone_hi"], chase=plan["chase"], setup_mode=plan["mode"],
        )
        sig.id = self.store.insert_signal(sig)
        events.append(self._emit("setup", sig, price))
        if not self.require_trigger:
            events.extend(self._activate(sig, price, now, "entered at market (trigger disabled)"))
        else:
            events.extend(self._try_trigger(sig, tech, m15, now))
        return events

    def _try_trigger(self, sig: TradeSignal, tech, m15, now: datetime) -> list[SignalEvent]:
        res = trig.evaluate(sig.side, sig.zone_lo, sig.zone_hi, tech.atr, m15, now)
        self.last_trigger = res.to_dict() | {"signal_id": sig.id}
        if res.touched:
            self._mark_touched(sig)
        if not res.fired:
            return []
        return self._activate(sig, res.entry or tech.price, now, res.note, atr=tech.atr)

    def _activate(self, sig: TradeSignal, entry: float, now: datetime, note: str, atr: float | None = None) -> list[SignalEvent]:
        """Turn a setup into a live call at ``entry`` – but only if the fill still makes sense:
        inside/near the zone, not past the chase level, not past TP1, stop on the right side."""
        entry = float(entry)
        long = sig.side == "BUY"
        if sig.zone_lo is not None and sig.zone_hi is not None and atr:
            slack = 0.5 * atr
            far = entry > sig.zone_hi + slack if long else entry < sig.zone_lo - slack
            if far:
                return [self._close(sig, "missed", entry, now)]
        if sig.chase is not None and ((long and entry > sig.chase) or (not long and entry < sig.chase)):
            return [self._close(sig, "missed", entry, now)]
        if (long and entry >= sig.tp1) or (not long and entry <= sig.tp1):
            return [self._close(sig, "missed", entry, now)]
        if (long and sig.sl >= entry) or (not long and sig.sl <= entry):
            return [self._close(sig, "invalidated", entry, now)]
        sig.entry = entry
        sig.status = "active"
        sig.triggered_at = now
        sig.trigger_note = note
        self.store.update_signal(sig)
        return [self._emit("triggered", sig, entry)]

    def on_price(self, price: float, now: datetime | None = None) -> list[SignalEvent]:
        """Called on every quote: SL / TP1 / TP2 / expiry for an open call; missed / invalidated for a pending one."""
        with self._lock:
            return self._on_price(price, now)

    def _on_price(self, price: float, now: datetime | None = None) -> list[SignalEvent]:
        now = now or datetime.now(timezone.utc)
        if price is None:
            return []
        events: list[SignalEvent] = []
        sig = self.active()
        if sig is not None:
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
            if now - (sig.triggered_at or sig.opened_at) > timedelta(hours=self.max_hours):
                events.append(self._close(sig, "expired", price, now))
            return events

        pend = self.pending()
        if pend is not None and pend.zone_lo is not None:
            long = pend.side == "BUY"
            if (long and price <= pend.sl) or (not long and price >= pend.sl):
                events.append(self._close(pend, "invalidated", price, now))
            elif pend.chase is not None and not pend.touched and ((long and price >= pend.chase) or (not long and price <= pend.chase)):
                events.append(self._close(pend, "missed", price, now))
            elif pend.zone_lo <= price <= pend.zone_hi:
                self._mark_touched(pend)
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
            sig = TradeSignal(id=0, side=side, entry=entry, sl=sl, tp1=tp1, tp2=tp2, confidence=conf, opened_at=opened, reason="demo",
                              status="active", zone_lo=entry - 4, zone_hi=entry + 2, chase=entry + 12 if side == "BUY" else entry - 12,
                              triggered_at=opened + timedelta(minutes=45), trigger_note="15m engulfing candle at the zone", setup_mode="pullback")
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
            self.store.add_signal_event(sig.id, "setup", entry - 2, f"{side} setup · zone {sig.zone_lo:,.1f}–{sig.zone_hi:,.1f}", at=opened)
            self.store.add_signal_event(sig.id, "triggered", entry, f"{side} XAU/USD @ {entry:,.1f} · {sig.trigger_note}", at=sig.triggered_at)
            self.store.add_signal_event(sig.id, sig.status, sig.exit_price, f"{sig.status} @ {sig.exit_price:,.1f} ({sig.result_r:+.2f}R)", at=sig.closed_at)
