"""
Web dashboard API (FastAPI).

GET  /               the dashboard page
GET  /api/report     latest analysis as JSON (candles, indicators, signal, news)
POST /api/refresh    force a new analysis run
GET  /api/health     liveness
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from market import indicators as ind
from news.store import article_to_dict
from pipeline import Report
from state import ReportCache

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


def report_to_json(rep: Report, history: list[dict], candles: int = 220) -> dict:
    h = rep.data.hourly
    tail = h.tail(candles)
    close = h["close"]
    e20 = ind.ema(close, 20).reindex(tail.index)
    e50 = ind.ema(close, 50).reindex(tail.index)
    e200 = ind.ema(close, 200).reindex(tail.index)

    def series(s):
        return [None if v != v else round(float(v), 2) for v in s.to_numpy()]

    t = rep.tech
    sig = rep.signal
    return {
        "generated_at": rep.generated_at.isoformat(),
        "bar_time": rep.data.last_bar_time.isoformat(),
        "source": rep.data.source,
        "price": {
            "last": round(t.price, 2),
            "change_1h_pct": round(t.change_1h_pct, 3),
            "change_24h_pct": round(t.change_24h_pct, 3),
            "change_7d_pct": round(t.change_7d_pct, 3),
            "atr": round(t.atr, 2),
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
            "rsi": round(t.rsi, 1),
            "macd_hist": round(t.macd_hist, 3),
            "bb_pct": round(t.bb_pct, 3) if t.bb_pct == t.bb_pct else None,
            "bb_squeeze": t.bb_squeeze,
            "ema20": round(t.ema20, 2),
            "ema50": round(t.ema50, 2),
            "ema200": round(t.ema200, 2),
            "daily_ema50": round(t.daily_ema50, 2),
            "daily_ema200": round(t.daily_ema200, 2),
            "reasons": t.reasons,
            "flags": t.flags,
            "supports": [round(s, 2) for s in t.supports],
            "resistances": [round(r, 2) for r in t.resistances],
            "pivots": {k: round(v, 2) for k, v in t.pivots.items()},
        },
        "news": {
            "score": rep.news.score,
            "label": rep.news.label,
            "n_articles": rep.news.n_articles,
            "n_bullish": rep.news.n_bullish,
            "n_bearish": rep.news.n_bearish,
            "n_neutral": rep.news.n_neutral,
            "themes": [{"theme": th, "value": round(v, 2)} for th, v in rep.news.top_themes],
            "articles": [article_to_dict(a) | {"age_hours": round(a.age_hours, 1)} for a in rep.news.drivers[:14]],
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
            "stop": round(sig.stop, 2) if sig.stop else None,
            "target1": round(sig.target1, 2) if sig.target1 else None,
            "target2": round(sig.target2, 2) if sig.target2 else None,
            "nearest_support": round(sig.nearest_support, 2) if sig.nearest_support else None,
            "nearest_resistance": round(sig.nearest_resistance, 2) if sig.nearest_resistance else None,
            "notes": sig.notes,
        },
        "briefing": rep.briefing,
        "events": [e.to_dict() for e in rep.events[:10]],
        "history": history[-60:],
    }


def signals_json(tracker) -> dict:
    active = tracker.active()
    return {
        "active": active.to_dict() if active else None,
        "history": [t.to_dict() for t in tracker.history(limit=20) if not t.is_open],
        "stats": tracker.stats(),
        "events": tracker.events(limit=20),
    }


def create_app(cache: ReportCache, demo: bool = False) -> FastAPI:
    from market.quote import QuoteService

    app = FastAPI(title="XAU Sentinel", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    quotes = QuoteService(demo=demo)

    @app.get("/api/quote")
    async def quote():
        if demo and cache.report is not None:
            quotes.set_demo_anchor(cache.report.tech.price)
        q = await asyncio.to_thread(quotes.get_quote)
        if q.get("price") is not None:
            try:
                await asyncio.to_thread(cache.tracker.on_price, q["price"])
            except Exception as exc:  # noqa: BLE001
                log.warning("tracker on_price failed: %s", exc)
        return JSONResponse(q | {"demo": demo})

    @app.get("/api/signals")
    async def signals():
        return JSONResponse(await asyncio.to_thread(signals_json, cache.tracker) | {"demo": demo})

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
            "last_run": rep.generated_at.isoformat() if rep else None,
            "stale": cache.is_stale(),
            "error": cache.last_error,
            "now": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/report")
    async def report():
        try:
            rep = await cache.get(with_briefing=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"analysis unavailable: {exc}") from exc
        return JSONResponse(report_to_json(rep, cache.history) | {"demo": demo, "signals": signals_json(cache.tracker)})

    @app.post("/api/refresh")
    async def refresh():
        try:
            rep = await cache.refresh(with_briefing=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"refresh failed: {exc}") from exc
        return JSONResponse(report_to_json(rep, cache.history) | {"demo": demo, "signals": signals_json(cache.tracker)})

    return app
