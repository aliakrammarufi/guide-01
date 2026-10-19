"""
Feeds live ticks into the trade-signal tracker (stop / take-profit / expiry checks),
at most once per second, from whichever tick source the engine is using.
"""
from __future__ import annotations

import asyncio
import logging
import time

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
