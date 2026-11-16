"""
Live news: headlines reach the clients as they are stored, not with the next report.

* ``EventHub``   – asyncio pub/sub for the server-sent events other than price ticks
                   (``news`` and ``signal``); the web layer drains it into /api/stream.
* ``news_watch`` – the fast scan cycle: the direct publisher / Fed feeds every
                   NEWS_LIVE_INTERVAL_SEC, every configured feed (Google News included) on the
                   usual NEWS_SCAN_INTERVAL_MIN cadence.

The scan itself stays in pipeline.Pipeline.scan_news (synchronous, run in a worker thread).
Whoever wants to hear about new articles registers a listener in ``pipeline.news_listeners``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from news.store import Article

log = logging.getLogger(__name__)

MIN_LIVE_INTERVAL_SEC = 20.0


def live_interval(settings) -> float:
    """The fast-cycle interval in seconds (never below 20); 0 when NEWS_LIVE_INTERVAL_SEC switches it off."""
    raw = float(getattr(settings, "news_live_interval_sec", 0) or 0)
    return max(MIN_LIVE_INTERVAL_SEC, raw) if raw > 0 else 0.0


class EventHub:
    """Fan-out of (kind, payload) events to every connected stream. Loop-bound like the tick engine:
    ``publish`` must run on the event loop; worker threads use ``publish_threadsafe``."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, kind: str, payload: dict) -> None:
        for q in list(self._subs):
            if q.full():                      # a slow reader loses its oldest event, never the newest
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait((kind, payload))
            except asyncio.QueueFull:
                pass

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, kind: str, payload: dict) -> None:
        try:
            loop.call_soon_threadsafe(self.publish, kind, payload)
        except RuntimeError:                  # the loop is closed (shutdown): nobody is listening any more
            pass


def sse_event(kind: str, payload: dict) -> str:
    """One named server-sent event. json.dumps never emits a raw newline, so the data stays on one line."""
    return f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"


def article_brief(a: Article) -> dict:
    """What a client needs to show a headline the moment it arrives (no summary: keeps the event small)."""
    return {
        "id": a.id, "title": a.title, "link": a.link, "source": a.source,
        "published_at": a.published_at.astimezone(timezone.utc).isoformat(),
        "fetched_at": a.fetched_at.astimezone(timezone.utc).isoformat(),
        "impact": a.impact, "confidence": a.confidence, "theme": a.theme, "reason": a.reason,
        "scored_by": a.scored_by, "high_impact": bool(a.high_impact), "direction": a.direction,
    }


async def news_watch(cache, hub: EventHub | None = None, every_sec: float | None = None) -> None:
    """Scan the direct feeds every ``every_sec`` (default: NEWS_LIVE_INTERVAL_SEC) and every feed once
    NEWS_SCAN_INTERVAL_MIN has passed since the last full scan, whoever ran it (the Telegram monitor job
    runs one too; ``min_gap_sec`` and the scan guard keep the two from doubling up).

    ``hub`` is not used here: new articles are announced by the listeners in ``pipeline.news_listeners``
    (the web app registers one that publishes to its hub, the bot one that sends the alerts).
    """
    pipe = cache.pipeline
    st = pipe.settings
    every = live_interval(st) if every_sec is None else max(MIN_LIVE_INTERVAL_SEC, float(every_sec))
    if every <= 0:
        log.info("Live news cycle is off (NEWS_LIVE_INTERVAL_SEC=0)")
        return
    full_gap = max(60.0, st.news_scan_interval_min * 60.0)
    log.info("Live news: %d direct feeds every %.0f s, all %d feeds every %.0f s", len(st.direct_feeds), every, len(st.feeds), full_gap)
    while True:
        await asyncio.sleep(every)
        try:
            direct = st.direct_feeds
            last_full = pipe.last_full_scan_at
            if last_full is None or (datetime.now(timezone.utc) - last_full).total_seconds() >= full_gap:
                await asyncio.to_thread(pipe.scan_news, None, full_gap - 5.0)     # covers the direct feeds too
            elif direct:
                await asyncio.to_thread(pipe.scan_news, direct)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("live news scan failed: %s", exc)
