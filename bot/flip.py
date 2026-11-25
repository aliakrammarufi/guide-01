"""
The flip account: a small pretend-or-real account (default $200) that follows the bot's confirmed calls with a
fixed share of its balance at risk on every trade, so the lot size grows as the balance does.

It is bookkeeping and wording only – the bot places no orders. Every confirmed call ("triggered") opens a flip
position sized from the current balance; the call's own exits close it:

  lots      balance × risk % ÷ (stop distance × contract size), rounded DOWN to 0.01, never below 0.01
  TP1       with 0.02 lots or more, half (rounded down to 0.01) is booked at TP1 as the call advises;
            a single 0.01 lot cannot be split and runs whole to the exit
  result    from the real entry / TP1 / exit prices and the lots held, minus the spread – not from the call's R,
            which assumes the half-booking that a 0.01 lot cannot do

With $200 the smallest position (0.01 lot = 1 oz) on a typical $30 stop already risks 15% of the account: the
risk shown on each signal is what the trade really risks, which can be more than the setting.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from html import escape

STATE_KEY = "flip_account"
MIN_LOT = 0.01
CLOSE_KINDS = ("tp2", "stopped", "flipped", "expired")


class FlipAccount:
    def __init__(self, store, start: float = 200.0, risk_pct: float = 15.0, goal: float = 20000.0,
                 contract_oz: float = 100.0, spread_usd: float = 0.30):
        self.store = store
        self.start = float(start)
        self.risk_pct = max(0.1, float(risk_pct))
        self.goal = float(goal)
        self.oz = float(contract_oz)
        self.spread = float(spread_usd)

    # ---------------------------------------------------------------- state
    def state(self) -> dict:
        d = self.store.get_state(STATE_KEY)
        if not isinstance(d, dict) or "balance" not in d:
            d = {"balance": self.start, "start": self.start, "open": None, "trades": [], "peak": self.start,
                 "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        return d

    def _save(self, d: dict) -> None:
        d["trades"] = d.get("trades", [])[-300:]
        self.store.set_state(STATE_KEY, d)

    def reset(self) -> dict:
        d = {"balance": self.start, "start": self.start, "open": None, "trades": [], "peak": self.start,
             "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._save(d)
        return d

    # --------------------------------------------------------------- sizing
    def size(self, balance: float, entry: float, stop: float) -> dict:
        """Lots for a trade from ``entry`` with ``stop``. ``ok`` is False when even 0.01 lot would risk the whole balance."""
        dist = abs(float(entry) - float(stop))
        per_lot = dist * self.oz                                   # $ lost per 1.00 lot if the stop is hit
        if per_lot <= 0 or balance <= 0:
            return {"ok": False, "lots": 0.0, "risk_usd": 0.0, "risk_pct": 0.0, "stop_dist": dist}
        lots = math.floor((balance * self.risk_pct / 100.0) / per_lot / MIN_LOT + 1e-9) * MIN_LOT
        lots = round(max(MIN_LOT, lots), 2)
        risk = lots * per_lot + lots * self.oz * self.spread
        return {"ok": risk < balance, "lots": lots, "risk_usd": round(risk, 2), "risk_pct": round(risk / balance * 100.0, 1),
                "stop_dist": round(dist, 2)}

    # --------------------------------------------------------------- events
    def on_event(self, e) -> str | None:
        """Update the account for one tracker event and return the message for the flip chat (None = nothing to say)."""
        s, d = e.signal, self.state()
        op = d.get("open")
        if e.kind == "triggered":
            if op is not None and op.get("id") == s.id:             # the same call announced twice
                return None
            if op is not None:                                      # a position whose call never reported its close
                d["open"] = None                                    # (bot down at the time): dropped without a result
            sz = self.size(d["balance"], s.entry, s.sl)
            if not sz["ok"]:
                return self._fmt_skip(d, s, sz)
            d["open"] = {"id": s.id, "side": s.side, "entry": s.entry, "sl": s.sl, "tp1": s.tp1, "tp2": s.tp2, "lots": sz["lots"],
                         "risk_usd": sz["risk_usd"], "risk_pct": sz["risk_pct"], "half": self._half(sz["lots"]),
                         "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self._save(d)
            return self._fmt_entry(d, d["open"], s)
        if op is None or op.get("id") != s.id:
            return None
        if e.kind == "tp1":
            return self._fmt_tp1(op)
        if e.kind in CLOSE_KINDS:
            exit_px = s.exit_price if s.exit_price is not None else e.price
            pnl = self.pnl(op, exit_px, tp1_hit=getattr(s, "tp1_hit_at", None) is not None)
            before = d["balance"]
            d["balance"] = round(max(0.0, before + pnl), 2)
            d["peak"] = max(d.get("peak", before), d["balance"])
            d["open"] = None
            d.setdefault("trades", []).append({"id": s.id, "side": op["side"], "lots": op["lots"], "pnl": round(pnl, 2),
                                               "balance": d["balance"], "kind": e.kind,
                                               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            self._save(d)
            return self._fmt_close(d, op, e.kind, exit_px, pnl, before)
        return None

    @staticmethod
    def _half(lots: float) -> float:
        """What can be booked at TP1: half, rounded down to 0.01 (nothing for a single 0.01 lot)."""
        return round(math.floor(lots / 2.0 / MIN_LOT + 1e-9) * MIN_LOT, 2)

    def pnl(self, op: dict, exit_px: float, tp1_hit: bool) -> float:
        sign = 1.0 if op["side"] == "BUY" else -1.0
        half = op.get("half", 0.0) if tp1_hit else 0.0
        rest = op["lots"] - half
        gross = sign * (op["tp1"] - op["entry"]) * self.oz * half + sign * (float(exit_px) - op["entry"]) * self.oz * rest
        return gross - op["lots"] * self.oz * self.spread

    # ------------------------------------------------------------- wording
    def _progress(self, d: dict) -> str:
        bal = d["balance"]
        mult = bal / d["start"] if d["start"] else 0.0
        return f"${bal:,.2f} ({mult:.2f}× of ${d['start']:,.0f}) · goal ${self.goal:,.0f}"

    def _fmt_entry(self, d: dict, op: dict, s) -> str:
        arrow = "🟢 BUY" if op["side"] == "BUY" else "🔴 SELL"
        over = ""
        if op["risk_pct"] > self.risk_pct + 0.5:
            over = f"\n<i>The smallest position (0.01 lot) risks more than the {self.risk_pct:g}% setting on this stop.</i>"
        half = (f"At TP1 close <b>{op['half']:.2f}</b> lots, let the rest run to TP2." if op["half"] >= MIN_LOT
                else "One 0.01 lot cannot be split: hold it to TP2 or the stop.")
        return (f"⚡ <b>FLIP — {arrow} NOW</b> · call #{op['id']}\n"
                f"Entry <b>{op['entry']:,.1f}</b> · Stop <b>{op['sl']:,.1f}</b>\n"
                f"TP1 <b>{op['tp1']:,.1f}</b> · TP2 <b>{op['tp2']:,.1f}</b>\n"
                f"Lot size <b>{op['lots']:.2f}</b> · risk <b>${op['risk_usd']:,.2f}</b> ({op['risk_pct']:g}% of the flip account)\n"
                f"{half}{over}\n"
                f"Flip balance: {self._progress(d)}\n"
                f"<i>High-risk challenge. Decision support only — this bot never places orders.</i>")

    def _fmt_tp1(self, op: dict) -> str:
        if op["half"] >= MIN_LOT:
            return (f"🎯 <b>FLIP — TP1 HIT</b> · call #{op['id']}\nClose <b>{op['half']:.2f}</b> lots at {op['tp1']:,.1f}. "
                    f"The other {op['lots'] - op['half']:.2f} runs to TP2 {op['tp2']:,.1f}; the stop stays at {op['sl']:,.1f}.")
        return (f"🎯 <b>FLIP — TP1 REACHED</b> · call #{op['id']}\nThe single 0.01 lot stays open to TP2 {op['tp2']:,.1f}; "
                f"the stop stays at {op['sl']:,.1f}.")

    def _fmt_close(self, d: dict, op: dict, kind: str, exit_px: float, pnl: float, before: float) -> str:
        how = {"tp2": "TP2 hit", "stopped": "stopped out", "flipped": "closed, bias flipped", "expired": "closed at max hold"}.get(kind, kind)
        icon, word = ("✅", "WIN") if pnl > 0.005 else (("❌", "LOSS") if pnl < -0.005 else ("➖", "FLAT"))
        sign = "+" if pnl >= 0 else "−"
        pct = (pnl / before * 100.0) if before else 0.0
        out = [f"{icon} <b>FLIP — {word} {sign}${abs(pnl):,.2f}</b> ({pct:+.1f}%) · call #{op['id']}",
               f"{op['side']} {op['lots']:.2f} lots · {op['entry']:,.1f} → {float(exit_px):,.1f} · {how}",
               f"Flip balance: <b>{self._progress(d)}</b>"]
        wins = sum(1 for t in d.get("trades", []) if t["pnl"] > 0.005)
        out.append(f"Flip record: {wins} wins / {len(d.get('trades', [])) - wins} others in {len(d.get('trades', []))} trades")
        if d["balance"] >= self.goal:
            out.append(f"🏆 <b>GOAL REACHED: ${self.goal:,.0f}.</b>")
        elif not self.size(d["balance"], op["entry"], op["sl"])["ok"]:
            out.append("💥 <b>The flip account can no longer afford the smallest position.</b> /flipreset starts a new one.")
        return "\n".join(out)

    def _fmt_skip(self, d: dict, s, sz: dict) -> str:
        return (f"⚠️ <b>FLIP — call #{s.id} skipped</b>\nThe smallest position (0.01 lot) would risk ${sz['risk_usd']:,.2f} on this stop, "
                f"and the flip account holds ${d['balance']:,.2f}. /flipreset starts a new one.")

    def status_text(self) -> str:
        d = self.state()
        trades = d.get("trades", [])
        wins = sum(1 for t in trades if t["pnl"] > 0.005)
        lines = ["⚡ <b>FLIP ACCOUNT</b>", f"Balance: <b>{self._progress(d)}</b>",
                 f"Risk per trade: {self.risk_pct:g}% of the balance (never less than one 0.01 lot)",
                 f"Trades: {len(trades)} · {wins} wins · best balance ${d.get('peak', d['balance']):,.2f}"]
        op = d.get("open")
        if op:
            lines.append(f"Open: {escape(op['side'])} {op['lots']:.2f} lots from {op['entry']:,.1f} · stop {op['sl']:,.1f} · TP2 {op['tp2']:,.1f}")
        for t in trades[-5:][::-1]:
            lines.append(f"{'✅' if t['pnl'] > 0.005 else '❌'} #{t['id']} {t['side']} {t['lots']:.2f} · "
                         f"{'+' if t['pnl'] >= 0 else '−'}${abs(t['pnl']):,.2f} → ${t['balance']:,.2f}")
        lines.append("<i>High-risk challenge: most flip accounts are lost. Only money you can afford to lose.</i>")
        return "\n".join(lines)
