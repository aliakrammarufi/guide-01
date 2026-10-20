"""
Historical replay of the signal rules ("self-check").

Runs the exact production code path – technical analysis → signal → setup zone →
15-minute confirmation → stop / targets – over the last weeks of real bars, so
the trader can see how the rules would have done before trusting them, and so
threshold changes can be judged on numbers instead of feelings.

What it is **not**: a promise. The replay is technical-only (news flow, calendar
blackouts and spreads are not available historically), entries are taken at the
15-minute close that confirms them, stops and targets fill at their level, and
within a bar the stop is checked before the targets (the conservative reading of
an ambiguous bar). Results count half the position booked at TP1, as the call
advises. Real results will differ; the numbers are for judging the rules, not
for forecasting.

The replay never touches the live database: it runs a private in-memory tracker.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable

import pandas as pd

from market.analysis import analyze_technicals
from market.data import MarketData
from news.sentiment import NewsScore
from news.store import Store
from signals.engine import build_signal
from signals.session import session_info
from signals.tracker import NO_TRADE_STATUSES, SignalTracker

log = logging.getLogger(__name__)

STATE_KEY = "backtest"
H1_WINDOW = 700      # hourly bars handed to the analysis (EMA200 needs a few hundred to settle)
D1_WINDOW = 400
M15_WINDOW = 400


@dataclass
class ReplayParams:
    min_conf: int = 45
    max_hours: float = 48.0
    pending_max_hours: float = 12.0
    breakeven_after_tp1: bool = True
    require_trigger: bool = True
    atr_stop_mult: float = 1.5
    atr_target_mult: float = 2.5
    tech_weight: float = 0.6
    news_weight: float = 0.4
    session_filter: bool = True
    asia_entries: bool = True
    spread_usd: float = 0.30              # round-trip spread charged to every trade ($/oz)
    slippage_usd: float = 0.10            # extra cost on a stop fill ($/oz)
    macro: bool = True                    # dollar / yields / equities context in the score (needs their daily history)
    trigger_kinds: tuple = ()             # () = every confirmation type
    zone_refine: bool = True              # fib / order-block zone refinement
    analysis_every_min: int = 15          # how often the analysis re-runs (production: every few minutes)
    yield_sleep: float = 0.0              # sleep per step so a live server stays responsive
    method: int = 5                       # replay/accounting/engine version: a change invalidates stored results

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "yield_sleep"}
        d["trigger_kinds"] = list(d["trigger_kinds"])
        return d


def _utc(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.to_datetime(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = df.copy()
    out.index = idx
    return out.sort_index()


def _agg(bars: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame | None:
    """One partial bar (open/high/low/close/volume) from ``bars``, stamped at ``start``."""
    if bars.empty:
        return None
    row = {"open": float(bars["open"].iloc[0]), "high": float(bars["high"].max()), "low": float(bars["low"].min()),
           "close": float(bars["close"].iloc[-1]), "volume": float(bars["volume"].sum())}
    return pd.DataFrame([row], index=pd.DatetimeIndex([start]))


def _bar_path(sig, lo: float, hi: float, close: float) -> list[float]:
    """The prices to feed the tracker for one bar.

    Open trade: orders fill *at their level*, not at the bar's extreme – a stop that is
    touched fills at the stop, a target at the target. The ambiguous order inside a bar is
    read conservatively: the stop first; after TP1 moves the stop to break-even, a bar that
    also reaches back to the entry is taken as a break-even stop, not as a run to TP2.
    Pending setup: the extremes (touch / chase / invalidation use levels, no P&L).
    """
    if not sig.is_open:
        return [lo, hi, close] if sig.side == "BUY" else [hi, lo, close]
    long = sig.side == "BUY"
    adverse, favourable = (lo, hi) if long else (hi, lo)
    beyond = (lambda level: adverse <= level) if long else (lambda level: adverse >= level)   # adverse side reached level
    reached = (lambda level: favourable >= level) if long else (lambda level: favourable <= level)
    path: list[float] = []
    if beyond(sig.effective_sl):
        return [sig.effective_sl]
    if sig.status == "active" and reached(sig.tp1):
        path.append(sig.tp1)                       # TP1 books half, stop → break-even
        if beyond(sig.entry):
            path.append(sig.entry)                 # the same bar came back to the entry: break-even stop
            return path
    if reached(sig.tp2):
        path.append(sig.tp2)
        return path
    path.append(close)
    return path


def _confluence_bucket(n: int) -> str:
    return "3+" if n >= 3 else str(int(n))


def _conf_bucket(c: int) -> str:
    return "65+" if c >= 65 else "55–64" if c >= 55 else "45–54" if c >= 45 else "<45"


def replay(h1: pd.DataFrame, d1: pd.DataFrame, m15: pd.DataFrame, params: ReplayParams | None = None,
           progress: Callable[[int, int], None] | None = None) -> dict:
    """Replay the rules over ``m15`` (15-minute bars) with ``h1``/``d1`` as context. Returns a JSON-able report."""
    p = params or ReplayParams()
    h1, d1, m15 = _utc(h1), _utc(d1), _utc(m15)
    store = Store(":memory:")
    tracker = SignalTracker(store, min_conf=p.min_conf, max_hours=p.max_hours, breakeven_after_tp1=p.breakeven_after_tp1,
                            pending_max_hours=p.pending_max_hours, require_trigger=p.require_trigger,
                            spread_usd=p.spread_usd, slippage_usd=p.slippage_usd, trigger_kinds=tuple(p.trigger_kinds) or None)
    tracker.quiet = True
    news = NewsScore(score=0, n_articles=0, n_bullish=0, n_bearish=0, n_neutral=0, top_themes=[], drivers=[])
    step = timedelta(minutes=15)
    h1_idx, d1_idx, m15_idx = h1.index, d1.index, m15.index
    h1_end = h1_idx + timedelta(hours=1)        # when each hourly bar is complete
    analysis_every = max(1, p.analysis_every_min // 15)

    # start once there is enough context (≈ 250 hourly bars and 40 daily bars before the first step)
    first_ok = None
    for i in range(len(m15)):
        t = m15.index[i] + step
        if (h1_end <= t).sum() >= 250 and (d1_idx < t.normalize()).sum() >= 40:
            first_ok = i
            break
    if first_ok is None or len(m15) - first_ok < 100:
        raise ValueError("not enough history for a replay (need ~11 days of hourly bars before the 15-minute data)")

    macro_tbl = None
    if p.macro:
        try:
            from market.macro import macro_table
            macro_tbl = macro_table(m15.index[first_ok], m15.index[-1])
        except Exception as exc:  # noqa: BLE001
            log.warning("replay: macro context unavailable (%s) – running without it", exc)
    meta: dict[int, dict] = {}          # signal id → session / trigger info
    last_tech = None
    total = len(m15) - first_ok
    started = time.monotonic()
    for n, i in enumerate(range(first_ok, len(m15))):
        bar = m15.iloc[i]
        t = m15.index[i] + step                 # this bar has just closed
        tnow = t.to_pydatetime()
        # ---- price path of the bar
        live = tracker.live()
        if live is not None:
            lo, hi, c = float(bar["low"]), float(bar["high"]), float(bar["close"])
            for px in _bar_path(live, lo, hi, c):
                if tracker.on_price(px, now=tnow):
                    live = tracker.live()
                    if live is None:
                        break
        # ---- analysis (with the in-progress hourly / daily bar, like production)
        if n % analysis_every == 0:
            k = int(h1_end.searchsorted(t, side="right"))
            hourly = h1.iloc[max(0, k - H1_WINDOW):k]
            hour_start = t.floor("h")
            if hour_start < t:
                j0 = int(m15_idx.searchsorted(hour_start, side="left"))
                part = _agg(m15.iloc[j0:i + 1], hour_start)
                if part is not None and (hourly.empty or part.index[0] > hourly.index[-1]):
                    hourly = pd.concat([hourly, part])
            kd = int(d1_idx.searchsorted(t.normalize(), side="left"))
            daily = d1.iloc[max(0, kd - D1_WINDOW):kd]
            j0 = int(m15_idx.searchsorted(t.normalize(), side="left"))
            part = _agg(m15.iloc[j0:i + 1], t.normalize())
            if part is not None and (daily.empty or part.index[0] > daily.index[-1]):
                daily = pd.concat([daily, part])
            m15_slice = m15.iloc[max(0, i + 1 - M15_WINDOW):i + 1]
            data = MarketData(hourly=hourly, daily=daily, source="replay", fetched_at=tnow, m15=m15_slice)
            if macro_tbl is not None:
                day = t.normalize()
                if day in macro_tbl.index:
                    data.macro = macro_tbl.at[day, "state"]
            try:
                tech = analyze_technicals(data)
            except Exception as exc:  # noqa: BLE001
                log.debug("replay analysis failed at %s: %s", t, exc)
                continue
            last_tech = tech
            session = session_info(now=tnow, events=[], asia_entries=p.asia_entries) if p.session_filter else None
            sig = build_signal(tech, news, tech_weight=p.tech_weight, news_weight=p.news_weight,
                               atr_stop_mult=p.atr_stop_mult, atr_target_mult=p.atr_target_mult, session=session,
                               zone_refine=p.zone_refine)
            report = SimpleNamespace(signal=sig, tech=tech, session=session, data=data)
            for ev in tracker.on_analysis(report, now=tnow):
                m = meta.setdefault(ev.signal.id, {})
                if ev.kind == "setup":
                    m["session"] = session.name if session else "n/a"
                    m["mode"] = ev.signal.setup_mode
                    m["conf"] = ev.signal.confidence
                    m["confluence"] = len([c for c in (ev.signal.confluence or "").split(" · ") if c])
                elif ev.kind == "triggered":
                    m["trigger"] = ev.signal.trigger_note
                    m["trigger_session"] = session.name if session else "n/a"
        elif last_tech is not None:
            pend = tracker.pending()
            if pend is not None:
                m15_slice = m15.iloc[max(0, i + 1 - M15_WINDOW):i + 1]
                for ev in tracker._try_trigger(pend, last_tech, m15_slice, tnow):
                    if ev.kind == "triggered":
                        meta.setdefault(ev.signal.id, {})["trigger"] = ev.signal.trigger_note
        if progress and n % 200 == 0:
            progress(n, total)
        if p.yield_sleep:
            time.sleep(p.yield_sleep)

    took = time.monotonic() - started
    signals = store.signals(limit=100000)
    result = summarize(signals, meta, p)
    result["period"] = {"from": (m15.index[first_ok] + step).isoformat(timespec="minutes"),
                        "to": (m15.index[-1] + step).isoformat(timespec="minutes"),
                        "days": round(((m15.index[-1] - m15.index[first_ok]).total_seconds()) / 86400, 1),
                        "bars_15m": int(total)}
    result["took_sec"] = round(took, 1)
    store.close()
    return result


def summarize(signals: list, meta: dict[int, dict], p: ReplayParams) -> dict:
    """Statistics over the replayed calls (also usable on the live history)."""
    traded = [s for s in signals if s.traded and not s.is_open and s.result_r is not None]
    traded.sort(key=lambda s: s.closed_at or s.opened_at)
    no_trade = [s for s in signals if s.status in NO_TRADE_STATUSES]
    open_now = [s for s in signals if s.is_live]

    def block(rows: list) -> dict:
        rs = [s.result_r for s in rows]
        wins = [r for r in rs if r > 0.05]
        losses = [r for r in rs if r < -0.05]
        gross_win, gross_loss = sum(wins), -sum(losses)
        holds = [((s.closed_at - (s.triggered_at or s.opened_at)).total_seconds() / 3600.0) for s in rows if s.closed_at]
        return {
            "count": len(rows), "wins": len(wins), "losses": len(losses), "breakeven": len(rows) - len(wins) - len(losses),
            "win_rate": round(100.0 * len(wins) / len(rows), 1) if rows else None,
            "total_r": round(sum(rs), 2), "avg_r": round(sum(rs) / len(rows), 2) if rows else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 1e-9 else (None if not wins else 99.0),
            "tp1_rate": round(100.0 * sum(1 for s in rows if s.tp1_hit_at) / len(rows), 1) if rows else None,
            "avg_hold_h": round(sum(holds) / len(holds), 1) if holds else None,
        }

    # equity curve + max drawdown in R
    equity, peak, dd, cum = [], 0.0, 0.0, 0.0
    for s in traded:
        cum += s.result_r
        equity.append(round(cum, 2))
        peak = max(peak, cum)
        dd = max(dd, peak - cum)

    def group(key_fn) -> dict:
        out: dict[str, list] = {}
        for s in traded:
            out.setdefault(key_fn(s), []).append(s)
        return {k: block(v) for k, v in sorted(out.items(), key=lambda kv: kv[0])}

    def outcome_counts(rows: list) -> dict:
        c: dict[str, int] = {}
        for s in rows:
            c[s.status] = c.get(s.status, 0) + 1
        return dict(sorted(c.items()))

    thresholds = {}
    for x in (45, 50, 55, 60, 65, 70):
        rows = [s for s in traded if (meta.get(s.id, {}).get("conf", s.confidence) or 0) >= x]
        thresholds[str(x)] = block(rows) | {"max_drawdown_r": None}
    summary = block(traded) | {"max_drawdown_r": round(dd, 2), "setups": len(signals), "no_trade": len(no_trade),
                               "open_at_end": len(open_now), "no_trade_breakdown": outcome_counts(no_trade),
                               "outcomes": outcome_counts(traded)}
    trades = [{
        "side": s.side, "opened": s.opened_at.isoformat(timespec="minutes"),
        "entered": s.triggered_at.isoformat(timespec="minutes") if s.triggered_at else None,
        "closed": s.closed_at.isoformat(timespec="minutes") if s.closed_at else None,
        "entry": round(s.entry, 2), "sl": round(s.sl, 2), "tp1": round(s.tp1, 2), "tp2": round(s.tp2, 2),
        "exit": round(s.exit_price, 2) if s.exit_price is not None else None, "status": s.status,
        "r": round(s.result_r, 2), "conf": meta.get(s.id, {}).get("conf", s.confidence),
        "session": meta.get(s.id, {}).get("session", ""), "trigger": meta.get(s.id, {}).get("trigger", s.trigger_note),
        "mode": meta.get(s.id, {}).get("mode", s.setup_mode),
    } for s in traded[-60:]]
    span_days = ((traded[-1].closed_at - traded[0].opened_at).days if len(traded) >= 2 and traded[-1].closed_at else 0)
    by_period = {}
    if span_days > 150:
        by_period = {
            "by_year": group(lambda s: str(s.opened_at.year)),
            "by_quarter": group(lambda s: f"{s.opened_at.year} Q{(s.opened_at.month - 1) // 3 + 1}"),
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": p.to_dict(),
        "summary": summary,
        **by_period,
        "by_confidence": group(lambda s: _conf_bucket(meta.get(s.id, {}).get("conf", s.confidence) or 0)),
        "by_session": group(lambda s: meta.get(s.id, {}).get("session", "n/a")),
        "by_side": group(lambda s: s.side),
        "by_trigger": group(lambda s: (meta.get(s.id, {}).get("trigger", s.trigger_note) or "n/a")),
        "by_mode": group(lambda s: meta.get(s.id, {}).get("mode", s.setup_mode) or "n/a"),
        "by_confluence": group(lambda s: _confluence_bucket(meta.get(s.id, {}).get("confluence", len([c for c in (s.confluence or "").split(" · ") if c])))),
        "thresholds": thresholds,
        "equity": equity,
        "trades": trades,
        "note": (f"Technical-only replay of the production rules on real bars: news flow and calendar blackouts are not "
                 f"included; entries at the confirming 15-minute close, stops and targets fill at their level, within a bar the "
                 f"stop is checked before the targets; results book half the position at TP1 as the call advises and are net of a "
                 f"${p.spread_usd:.2f} round-trip spread and ${p.slippage_usd:.2f} slippage on stop fills. "
                 f"Past replay results do not predict future results."),
    }


# ---------------------------------------------------------------------------
# data + server-side runner
# ---------------------------------------------------------------------------
def fetch_history(symbol: str, m15_period: str = "60d", h1_period: str = "120d", d1_period: str = "1y") -> tuple:
    """Hourly, daily and 15-minute bars for the replay (yfinance; falls back through the usual symbols)."""
    import yfinance as yf

    from market.data import YF_FALLBACK_SYMBOLS, _normalize

    last: Exception | None = None
    for sym in [symbol] + [s for s in YF_FALLBACK_SYMBOLS if s != symbol]:
        try:
            tk = yf.Ticker(sym)
            h1 = _normalize(tk.history(period=h1_period, interval="1h", auto_adjust=False))
            d1 = _normalize(tk.history(period=d1_period, interval="1d", auto_adjust=False))
            m15 = _normalize(tk.history(period=m15_period, interval="15m", auto_adjust=False))
            return h1, d1, m15, sym
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("replay data: %s failed (%s)", sym, exc)
    raise RuntimeError(f"no history available: {last}")


def params_from_settings(settings) -> ReplayParams:
    return ReplayParams(
        min_conf=settings.signal_min_conf, max_hours=settings.signal_max_hours,
        pending_max_hours=settings.signal_pending_max_hours, breakeven_after_tp1=settings.signal_breakeven_after_tp1,
        require_trigger=settings.signal_require_trigger, atr_stop_mult=settings.atr_stop_mult,
        atr_target_mult=settings.atr_target_mult, tech_weight=settings.tech_weight, news_weight=settings.news_weight,
        session_filter=settings.session_filter, asia_entries=settings.asia_entries,
        spread_usd=settings.spread_usd, slippage_usd=settings.slippage_usd, macro=settings.macro_context,
        trigger_kinds=tuple(settings.trigger_kinds), zone_refine=settings.zone_refine,
    )


# Structurally different ways to manage the same calls, replayed on the same bars so they can be
# compared on numbers. Deliberately few and coarse: 70 days of data cannot support fine tuning.
VARIANTS: list[tuple[str, dict]] = [
    ("no break-even stop", {"breakeven_after_tp1": False}),
    ("closer targets (TP1 1.0 · TP2 2.0 ATR)", {"atr_target_mult": 2.0}),
    ("further targets (TP1 1.5 · TP2 3.0 ATR)", {"atr_target_mult": 3.0}),
    ("wider stop (2.0 ATR)", {"atr_stop_mult": 2.0}),
    ("no Asian-session entries", {"asia_entries": False}),
    ("no macro context", {"macro": False}),
    ("no engulfing confirmation", {"trigger_kinds": ("ema_reclaim", "pin_bar", "break")}),
    ("no zone refinement (fib / order block)", {"zone_refine": False}),
]
VARIANTS_KEY = "backtest_variants"


def run_and_store(settings, store: Store, yield_sleep: float = 0.01, sweep: bool = False) -> dict:
    """Fetch history, replay, save the report under ``state['backtest']``. Blocking – call from a thread.
    With ``sweep`` the VARIANTS are replayed on the same bars afterwards (state['backtest_variants'])."""
    h1, d1, m15, sym = fetch_history(settings.yf_symbol)
    p = params_from_settings(settings)
    p.yield_sleep = yield_sleep
    log.info("Self-check: replaying %d 15-minute bars of %s through the signal rules…", len(m15), sym)
    result = replay(h1, d1, m15, p, progress=lambda n, t: log.info("Self-check: %d/%d bars", n, t) if n and n % 2000 == 0 else None)
    result["symbol"] = sym
    store.set_state(STATE_KEY, result)
    _log_result(result)
    if sweep:
        run_variants(h1, d1, m15, p, store, base_summary=result["summary"])
    return result


def run_variants(h1, d1, m15, base: ReplayParams, store: Store, base_summary: dict | None = None, key: str = VARIANTS_KEY,
                 log_every: int = 0) -> dict:
    rows = []
    if base_summary is not None:
        rows.append({"name": "current rules", "overrides": {}, "summary": _brief(base_summary)})
    for name, overrides in VARIANTS:
        p = ReplayParams(**{**base.__dict__, **overrides})
        log.info("Self-check variant '%s'…", name)
        try:
            r = replay(h1, d1, m15, p, progress=(lambda n, t: log.info("Self-check variant: %d/%d bars", n, t) if n and n % log_every == 0 else None) if log_every else None)
        except Exception as exc:  # noqa: BLE001
            log.warning("Self-check variant '%s' failed: %s", name, exc)
            continue
        b = _brief(r["summary"])
        rows.append({"name": name, "overrides": overrides, "summary": b})
        log.info("Self-check variant '%s': trades %d · win %s%% · avg %sR · total %sR · PF %s · maxDD %sR",
                 name, b["count"], b["win_rate"], b["avg_r"], b["total_r"], b["profit_factor"], b["max_drawdown_r"])
        out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "base_params": base.to_dict(), "variants": rows,
               "complete": len(rows) >= len(VARIANTS) + (1 if base_summary is not None else 0)}
        store.set_state(key, out)                   # saved after every variant: a restart loses nothing
    return out if rows else {}


def _brief(s: dict) -> dict:
    keys = ("count", "wins", "losses", "breakeven", "win_rate", "avg_r", "total_r", "profit_factor", "max_drawdown_r", "tp1_rate", "setups", "no_trade")
    return {k: s.get(k) for k in keys}


def _log_result(result: dict) -> None:
    s = result["summary"]
    log.info("Self-check done in %.0fs: %d setups, %d trades, win rate %s%%, avg %sR, total %+.1fR, max drawdown %.1fR, no-trade %d",
             result["took_sec"], s["setups"], s["count"], s["win_rate"], s["avg_r"], s["total_r"], s["max_drawdown_r"], s["no_trade"])
    log.info("Self-check outcomes %s · no-trade %s · TP1 %s%% · PF %s", s["outcomes"], s["no_trade_breakdown"], s["tp1_rate"], s["profit_factor"])
    for key in ("by_confidence", "by_session", "by_trigger", "by_mode", "by_side", "by_confluence", "thresholds"):
        log.info("Self-check %s: %s", key, "; ".join(f"{k}: n={v['count']} win={v['win_rate']}% avg={v['avg_r']}R tot={v['total_r']}R"
                                                    for k, v in result[key].items() if v.get("count")))


def _fresh(stored: dict | None, base: ReplayParams, max_age_days: float, params_key: str) -> bool:
    if not stored or not stored.get("generated_at") or stored.get("complete") is False:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stored["generated_at"])
    except (TypeError, ValueError):
        return False
    return age < timedelta(days=max_age_days) and stored.get(params_key) == base.to_dict()


DEEP_KEY = "backtest_deep"
DEEP_VARIANTS_KEY = "backtest_deep_variants"


def run_deep(settings, store: Store, yield_sleep: float = 0.02) -> dict:
    """The deep self-check: the rules replayed over DEEP_CHECK_YEARS of Dukascopy 15-minute bars (spot basis).
    Blocking and slow (hours on a small instance) – call from a thread."""
    from market.history import load_history

    cache_path = Path(settings.db_path).parent / "history_m15.pkl.gz"
    h1, d1, m15 = load_history(years=settings.deep_check_years, cache_path=cache_path, redis_url=settings.redis_url,
                               progress=lambda n, t: log.info("Deep check: fetched %d/%d days", n, t) if n else None)
    p = params_from_settings(settings)
    p.yield_sleep = yield_sleep
    log.info("Deep check: replaying %d 15-minute bars (%s → %s) through the signal rules…", len(m15),
             m15.index[0].date(), m15.index[-1].date())
    result = replay(h1, d1, m15, p, progress=lambda n, t: log.info("Deep check: %d/%d bars", n, t) if n and n % 5000 == 0 else None)
    result["symbol"] = "XAU/USD spot (Dukascopy)"
    if len(result.get("equity") or []) > 1500:                       # keep the stored report small
        eq = result["equity"]
        step = max(1, len(eq) // 1500)
        result["equity"] = eq[::step] + ([eq[-1]] if eq[-1] != eq[::step][-1] else [])
    result["trades"] = result["trades"][-100:]
    store.set_state(DEEP_KEY, result)
    result["_data"] = (h1, d1, m15)                                  # for a following sweep; stripped by the caller
    s = result["summary"]
    log.info("Deep check done in %.0fs: %d setups, %d trades, win rate %s%%, avg %sR, total %+.1fR, PF %s, max drawdown %.1fR",
             result["took_sec"], s["setups"], s["count"], s["win_rate"], s["avg_r"], s["total_r"], s["profit_factor"], s["max_drawdown_r"])
    for key in ("by_year", "by_quarter", "by_session", "by_confluence", "by_trigger", "thresholds"):
        if key in result:
            log.info("Deep check %s: %s", key, "; ".join(f"{k}: n={v['count']} win={v['win_rate']}% avg={v['avg_r']}R tot={v['total_r']}R"
                                                       for k, v in result[key].items() if v.get("count")))
    return result


async def run_on_start(cache, delay_sec: int = 150) -> None:
    """Server-side: run the replay in a worker thread shortly after start-up when the stored one is stale
    (and the variant sweep when BACKTEST_SWEEP is on and its stored result is stale)."""
    from config import settings

    await asyncio.sleep(delay_sec)          # let the first live analysis and the Telegram bot settle first
    store = cache.pipeline.store
    base = params_from_settings(settings)
    main_fresh = _fresh(store.get_state(STATE_KEY), base, settings.backtest_max_age_days, "params")
    deep_due = settings.deep_check and not _fresh(store.get_state(DEEP_KEY), base, settings.backtest_max_age_days, "params")
    deep_sweep_due = settings.deep_check and settings.deep_sweep and \
        not _fresh(store.get_state(DEEP_VARIANTS_KEY), base, settings.backtest_max_age_days, "base_params")
    sweep_due = settings.backtest_sweep and not _fresh(store.get_state(VARIANTS_KEY), base, settings.backtest_max_age_days, "base_params")
    if main_fresh and not deep_due and not sweep_due and not deep_sweep_due:
        log.info("Self-check: stored replays are recent – skipping")
        return
    try:
        if not main_fresh:
            await asyncio.to_thread(run_and_store, settings, store, 0.01, False)
        if deep_due or deep_sweep_due:
            def _deep():
                data = None
                if deep_due:
                    res = run_deep(settings, store)
                    data = res.pop("_data", None)
                if deep_sweep_due:
                    if data is None:
                        from market.history import load_history
                        data = load_history(years=settings.deep_check_years, cache_path=Path(settings.db_path).parent / "history_m15.pkl.gz",
                                            redis_url=settings.redis_url)
                    h1, d1, m15 = data
                    p = params_from_settings(settings)
                    p.yield_sleep = 0.02
                    deep = store.get_state(DEEP_KEY) or {}
                    log.info("Deep check: replaying %d alternative managements over the same %d bars…", len(VARIANTS), len(m15))
                    run_variants(h1, d1, m15, p, store, base_summary=deep.get("summary"), key=DEEP_VARIANTS_KEY, log_every=20000)
            await asyncio.to_thread(_deep)
        if sweep_due:
            def _sweep_only():
                h1, d1, m15, _sym = fetch_history(settings.yf_symbol)
                p = params_from_settings(settings)
                p.yield_sleep = 0.01
                run_variants(h1, d1, m15, p, store, base_summary=store.get_state(STATE_KEY)["summary"])
            await asyncio.to_thread(_sweep_only)
    except Exception as exc:  # noqa: BLE001
        log.exception("Self-check failed: %s", exc)
