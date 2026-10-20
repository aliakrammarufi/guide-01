"""
Feeds live ticks into the trade-signal tracker (stop / take-profit / expiry checks),
at most once per second, from whichever tick source the engine is using.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)


async def tracker_feed(engine, tracker) -> None:
    q = engine.subscribe()
    last = 0.0
    try:
        while True:
            t = await q.get()
            if time.monotonic() - last < 1.0:
                continue
            last = time.monotonic()
            try:
                await asyncio.to_thread(tracker.on_price, t.price)
            except Exception as exc:  # noqa: BLE001
                log.warning("tracker on_price failed: %s", exc)
    finally:
        engine.unsubscribe(q)


async def bar_builder_feed(engine, builder) -> None:
    """Every tick into the live candle builder."""
    q = engine.subscribe()
    try:
        while True:
            t = await q.get()
            builder.on_tick(t.price, t.ts)
    finally:
        engine.unsubscribe(q)


async def bar_close_watch(cache, delay_sec: float = 4.0) -> None:
    """A fast chart read a few seconds after every 15-minute close, so a confirmation is noticed at once."""
    while True:
        now = time.time()
        nxt = (int(now // 900) + 1) * 900 + delay_sec
        await asyncio.sleep(max(1.0, nxt - now))
        try:
            rep = await cache.refresh(with_briefing=False, refresh_news=False, fast=True)
            live = cache.tracker.live()
            log.info("15-minute close read: %s %d%% · %s", rep.signal.bias, rep.signal.confidence,
                     f"{live.side} {live.status}" if live else "no call")
        except Exception as exc:  # noqa: BLE001
            log.warning("bar-close refresh failed: %s", exc)


async def minute_refresh(cache, every_sec: float = 60.0, first_delay: float = 120.0) -> None:
    """Keep the chart read current to the minute between the scheduled jobs (provider bars are cached, so
    this costs no extra network beyond the live ticks)."""
    await asyncio.sleep(first_delay)
    while True:
        await asyncio.sleep(every_sec)
        try:
            if cache.report is None:
                continue
            age = (datetime.now(timezone.utc) - cache.report.generated_at).total_seconds()
            if age < every_sec - 5:
                continue                      # something else refreshed it very recently
            await cache.refresh(with_briefing=False, refresh_news=False, fast=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("minute refresh failed: %s", exc)
