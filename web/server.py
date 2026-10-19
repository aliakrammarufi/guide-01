"""
Web dashboard API (FastAPI).

GET  /               the dashboard page
GET  /api/report     latest analysis as JSON (candles, indicators, signal, news)
POST /api/refresh    force a new analysis run
GET  /api/health     liveness
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from market import indicators as ind
from news.store import article_to_dict
from pipeline import Report
from state import ReportCache

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


def _r(x, d: int = 2):
    """round() that turns NaN/inf (short data, no provider) into null instead of invalid JSON."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (v != v or v in (float("inf"), float("-inf"))) else round(v, d)


def report_to_json(rep: Report, history: list[dict], candles: int = 220) -> dict:
    h = rep.data.hourly
    tail = h.tail(candles)
    close = h["close"]
    e20 = ind.ema(close, 20).reindex(tail.index)
    e50 = ind.ema(close, 50).reindex(tail.index)
    e200 = ind.ema(close, 200).reindex(tail.index)

    def series(s):
        return [None if v != v else _r(float(v), 2) for v in s.to_numpy()]

    t = rep.tech
    sig = rep.signal
    return {
        "generated_at": rep.generated_at.isoformat(),
        "bar_time": rep.data.last_bar_time.isoformat(),
        "source": rep.data.source,
        "price": {
            "last": _r(t.price, 2),
            "change_1h_pct": _r(t.change_1h_pct, 3),
            "change_24h_pct": _r(t.change_24h_pct, 3),
            "change_7d_pct": _r(t.change_7d_pct, 3),
            "atr": _r(t.atr, 2),
        },
        "candles": {
            "time": [int(ts.timestamp()) for ts in tail.index],
            "open": series(tail["open"]),
            "high": series(tail["high"]),
            "low": series(tail["low"]),
            "close": series(tail["close"]),
            "ema20": series(e20),
            "ema50": series(e50),
            "ema200": series(e200),
        },
        "technical": {
            "score": t.score,
            "trend": t.trend_label,
            "rsi": _r(t.rsi, 1),
            "macd_hist": _r(t.macd_hist, 3),
            "bb_pct": _r(t.bb_pct, 3) if t.bb_pct == t.bb_pct else None,
            "bb_squeeze": t.bb_squeeze,
            "ema20": _r(t.ema20, 2),
            "ema50": _r(t.ema50, 2),
            "ema200": _r(t.ema200, 2),
            "daily_ema50": _r(t.daily_ema50, 2),
            "daily_ema200": _r(t.daily_ema200, 2),
            "reasons": t.reasons,
            "flags": t.flags,
            "supports": [_r(s, 2) for s in t.supports],
            "resistances": [_r(r, 2) for r in t.resistances],
            "pivots": {k: _r(v, 2) for k, v in t.pivots.items()},
        },
        "news": {
            "score": rep.news.score,
            "label": rep.news.label,
            "n_articles": rep.news.n_articles,
            "n_bullish": rep.news.n_bullish,
            "n_bearish": rep.news.n_bearish,
            "n_neutral": rep.news.n_neutral,
            "themes": [{"theme": th, "value": _r(v, 2)} for th, v in rep.news.top_themes],
            "articles": [article_to_dict(a) | {"age_hours": _r(a.age_hours, 1)} for a in rep.news.drivers[:14]],
        },
        "signal": {
            "bias": sig.bias,
            "label": sig.label,
            "combined": sig.combined,
            "tech_score": sig.tech_score,
            "news_score": sig.news_score,
            "confidence": sig.confidence,
            "agreement": sig.agreement,
            "direction": sig.direction,
            "stop": _r(sig.stop, 2) if sig.stop else None,
            "target1": _r(sig.target1, 2) if sig.target1 else None,
            "target2": _r(sig.target2, 2) if sig.target2 else None,
            "zone_lo": sig.zone_lo, "zone_hi": sig.zone_hi, "zone_mid": sig.zone_mid, "chase_limit": sig.chase_limit,
            "entry_mode": sig.entry_mode, "planned_rr": sig.planned_rr, "raw_confidence": sig.raw_confidence,
            "nearest_support": _r(sig.nearest_support, 2) if sig.nearest_support else None,
            "nearest_resistance": _r(sig.nearest_resistance, 2) if sig.nearest_resistance else None,
            "notes": sig.notes,
        },
        "briefing": rep.briefing,
        "events": [e.to_dict() for e in rep.events[:10]],
        "history": history[-60:],
    }


def sizing_defaults() -> dict:
    from config import settings as st
    from market.fx import usd_rate
    fx, src = usd_rate(st.account_currency, cached_only=True) if st.account_size else (1.0, "fixed")
    return {"account": st.account_size, "currency": st.account_currency, "risk_pct": st.risk_pct,
            "fixed_lots": st.fixed_lots, "fx": fx, "fx_source": src, "contract_oz": st.contract_oz}


def selfcheck_summary(store) -> dict | None:
    """Compact headline numbers of the last historical replay (see signals/backtest.py)."""
    bt = store.get_state("backtest")
    if not bt or not isinstance(bt, dict) or "summary" not in bt:
        return None
    s = bt["summary"]
    return {"generated_at": bt.get("generated_at"), "period": bt.get("period"), "count": s.get("count"), "setups": s.get("setups"),
            "win_rate": s.get("win_rate"), "avg_r": s.get("avg_r"), "total_r": s.get("total_r"),
            "profit_factor": s.get("profit_factor"), "max_drawdown_r": s.get("max_drawdown_r"), "tp1_rate": s.get("tp1_rate")}


def signals_json(tracker, session=None) -> dict:
    live = tracker.live()
    return {
        "sizing": sizing_defaults(),
        "active": live.to_dict() if live else None,        # open trade or pending setup (see .status)
        "history": [t.to_dict() for t in tracker.history(limit=20) if not t.is_live],
        "stats": tracker.stats(),
        "events": tracker.events(limit=20),
        "trigger": tracker.last_trigger,
        "session": session.to_dict() if session is not None else None,
        "selfcheck": selfcheck_summary(tracker.store),
    }


def create_app(cache: ReportCache, demo: bool = False, engine=None) -> FastAPI:
    from market.quote import QuoteService

    app = FastAPI(title="XAU Sentinel", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    basis: dict = {"key": None, "value": 1.0}

    def prev_close_for(spot: float) -> float | None:
        """Previous daily close, scaled to the live tick's basis.

        The live tick is spot XAU/USD; the bars may be COMEX futures (GC=F), which trade
        at a premium. The ratio spot / last hourly close (captured once per analysis
        refresh, when the hourly bar is fresh) converts the bar-based previous close
        to the spot basis so "today's change" is right and ticks with the price.
        """
        rep = cache.report
        if rep is None or rep.data is None:
            return None
        daily, hourly = rep.data.daily, rep.data.hourly
        if daily is None or len(daily) < 2:
            return None
        last_day = daily.index[-1]
        today = pd.Timestamp.now(tz="UTC").normalize()
        prev = float(daily["close"].iloc[-2] if last_day.tz_convert("UTC").normalize() >= today else daily["close"].iloc[-1])
        key = (rep.data.last_bar_time, rep.data.source)
        if basis["key"] != key and hourly is not None and len(hourly) and spot:
            ref = float(hourly["close"].iloc[-1])
            ratio = spot / ref if ref else 1.0
            basis["key"], basis["value"] = key, (ratio if abs(ratio - 1.0) > 0.0015 else 1.0)
        return prev * basis["value"]

    quotes = QuoteService(demo=demo, prev_close_fn=None if demo else prev_close_for)

    # ---- real-time tick engine (websocket feeds → SSE to the browser) --------------
    from market.ticks import TickEngine

    owns_engine = engine is None and not demo
    if engine is None:
        engine = TickEngine()

    def tick_json(t) -> dict:
        """A tick plus the fields the page needs: previous close / today's change."""
        d = t.to_dict()
        prev = None
        try:
            prev = prev_close_for(t.price)
        except Exception:  # noqa: BLE001
            prev = None
        prev = prev or t.price
        d.update({"prev_close": round(prev, 2), "change_pct": round((t.price / prev - 1) * 100, 3) if prev else 0.0,
                  "stale": False, "demo": demo, "stream": True})
        return d

    @app.on_event("startup")
    async def _start_ticks():
        if owns_engine:                      # main.py normally owns the engine and the tracker feed
            from signals.live import tracker_feed
            await engine.start()
            asyncio.create_task(tracker_feed(engine, cache.tracker), name="tracker-feed")

    @app.on_event("shutdown")
    async def _stop_ticks():
        if owns_engine:
            await engine.stop()

    @app.get("/api/stream")
    async def stream(request: Request):
        """Server-sent events: one message per tick, instantly."""
        if demo:
            raise HTTPException(404, "no live stream in demo mode")
        q = engine.subscribe()

        async def gen():
            try:
                t = engine.latest()
                yield f"data: {json.dumps(tick_json(t))}\n\n" if t else ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        t = await asyncio.wait_for(q.get(), timeout=10)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(tick_json(t))}\n\n"
            finally:
                engine.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    @app.get("/api/quote")
    async def quote():
        if demo and cache.report is not None:
            quotes.set_demo_anchor(cache.report.tech.price)
        live = None if demo else engine.latest()
        if live is not None and time.monotonic() - live.seen <= 20:
            q = tick_json(live) | {"interval_ms": 1000}
            return JSONResponse(q)
        q = await asyncio.to_thread(quotes.get_quote)
        if demo and q.get("price") is not None:        # live mode: the tick engine feeds the tracker (spot only)
            try:
                await asyncio.to_thread(cache.tracker.on_price, q["price"])
            except Exception as exc:  # noqa: BLE001
                log.warning("tracker on_price failed: %s", exc)
        return JSONResponse(q | {"demo": demo})

    @app.get("/api/signals")
    async def signals():
        rep = cache.report
        return JSONResponse(await asyncio.to_thread(signals_json, cache.tracker, rep.session if rep else None) | {"demo": demo})

    @app.get("/api/backtest")
    @app.get("/api/selfcheck")
    async def backtest():
        """The last historical replay of the signal rules (technical-only self-check)."""
        bt = await asyncio.to_thread(cache.pipeline.store.get_state, "backtest")
        if not bt:
            raise HTTPException(status_code=404, detail="no self-check has run yet")
        return JSONResponse(bt, headers={"Cache-Control": "no-store"})

    @app.get("/api/tape")
    async def tape():
        t = await asyncio.to_thread(quotes.get_tape)
        return JSONResponse({"items": t, "demo": demo})

    @app.get("/api/calendar")
    async def calendar():
        events = await asyncio.to_thread(cache.pipeline.calendar.upcoming, 72)
        return JSONResponse({"events": [e.to_dict() for e in events[:20]], "demo": demo})

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    async def health():
        rep = cache.report
        return {
            "ok": True,
            "demo": demo,
            "ticks": {"source": engine.active_source(), "updates": engine.updates, "per_source": engine.stats},
            "last_run": rep.generated_at.isoformat() if rep else None,
            "stale": cache.is_stale(),
            "error": cache.last_error,
            "backup": cache.backup.status() if getattr(cache, "backup", None) else {"enabled": False},
            "now": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/report")
    async def report():
        try:
            rep = await cache.get(with_briefing=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"analysis unavailable: {exc}") from exc
        return JSONResponse(report_to_json(rep, cache.history) | {"demo": demo, "signals": signals_json(cache.tracker, rep.session)})

    @app.post("/api/refresh")
    async def refresh():
        try:
            rep = await cache.refresh(with_briefing=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"refresh failed: {exc}") from exc
        return JSONResponse(report_to_json(rep, cache.history) | {"demo": demo, "signals": signals_json(cache.tracker, rep.session)})

    return app
