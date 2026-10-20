"""
XAU Sentinel – entry point.

    python main.py                 # Telegram bot + web dashboard (http://localhost:8080)
    python main.py --demo          # synthetic data, no keys needed – try the dashboard
    python main.py --no-web        # Telegram only
    python main.py --no-telegram   # dashboard only
    python main.py --once          # run one analysis, print it, exit (good for cron / testing)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from config import settings

log = logging.getLogger("sentinel")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="XAU Sentinel – gold market analysis bot")
    p.add_argument("--demo", action="store_true", help="use synthetic data and sample news (no keys needed)")
    p.add_argument("--no-telegram", action="store_true", help="do not start the Telegram bot")
    p.add_argument("--no-web", action="store_true", help="do not start the web dashboard")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")), help="web port (defaults to $PORT or 8080)")
    p.add_argument("--once", action="store_true", help="run a single analysis, print the report and exit")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    import backup as state_backup
    from news.store import Store
    from state import ReportCache

    if settings.redis_url and not args.demo:
        # A fresh container has no database: pull the last snapshot before opening it.
        await asyncio.to_thread(state_backup.restore, settings.db_path, settings.redis_url)
    store = Store(settings.db_path)
    if args.demo:
        from demo import DemoPipeline

        pipeline = DemoPipeline(settings, store)
        log.info("DEMO MODE – synthetic prices and sample headlines")
    else:
        from pipeline import Pipeline

        pipeline = Pipeline(settings, store)

    cache = ReportCache(pipeline, max_age_sec=max(60, settings.analysis_interval_min * 60))

    if args.once:
        import html
        import re

        from bot import formatting as fmt

        rep = await cache.refresh(with_briefing=True)
        text = fmt.format_full_report(rep.tech, rep.news, rep.signal, rep.briefing, rep.data.source, rep.data.last_bar_time, events=rep.events, trade=cache.tracker.active())
        print(html.unescape(re.sub(r"<[^>]+>", "", text)))
        return 0

    tasks: list[asyncio.Task] = []

    # Keep a copy of the database in the key/value store (free hosts wipe the disk on redeploy).
    backup = state_backup.StateBackup(store, "" if args.demo else settings.redis_url, settings.backup_interval_sec)
    cache.backup = backup
    if backup.enabled:
        tasks.append(asyncio.create_task(backup.run(), name="backup"))

    # Live tick engine (spot XAU/USD) + the tracker feed run whether or not the web terminal is on,
    # so stop / take-profit checks always see every tick.
    engine = None
    if not args.demo:
        from market import bars as live_bars
        from market.ticks import TickEngine
        from signals.live import bar_builder_feed, bar_close_watch, minute_refresh, tracker_feed

        engine = TickEngine()
        await engine.start()
        tasks.append(asyncio.create_task(tracker_feed(engine, cache.tracker), name="tracker-feed"))
        # Candles from the tick stream (current to the second) + a chart read seconds after every 15-minute
        # close and every minute in between – so confirmations are caught at once, not minutes later.
        live_bars.LIVE = live_bars.BarBuilder()
        tasks.append(asyncio.create_task(bar_builder_feed(engine, live_bars.LIVE), name="bar-builder"))
        tasks.append(asyncio.create_task(bar_close_watch(cache), name="bar-close"))
        tasks.append(asyncio.create_task(minute_refresh(cache), name="minute-refresh"))

    async def guarded(coro, name: str):
        """A crash in one component (e.g. an invalid Telegram token) must not take the others down."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("%s stopped with an error – the other components keep running", name)
            while True:
                await asyncio.sleep(3600)

    if not args.no_telegram:
        if not settings.telegram_token:
            log.error("TELEGRAM_BOT_TOKEN is not set – starting without Telegram (use --no-telegram to silence this)")
        else:
            from bot.telegram_bot import SentinelBot

            bot = SentinelBot(settings, store, cache)
            tasks.append(asyncio.create_task(guarded(bot.run_forever(), "Telegram"), name="telegram"))

    stop = asyncio.Event()          # set on SIGTERM / SIGINT → orderly shutdown (final backup, stop polling)
    import signal as _signal

    loop = asyncio.get_running_loop()
    for sig in (_signal.SIGTERM, _signal.SIGINT):
        # Installed before uvicorn starts: uvicorn captures the signals while serving, restores these
        # handlers afterwards and re-raises the signal – which then lands here instead of killing us.
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    if not args.no_web:
        import uvicorn

        from web.server import create_app

        app = create_app(cache, demo=args.demo, engine=engine)
        config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", loop="asyncio")
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve(), name="web"))
        log.info("Dashboard: http://localhost:%d", args.port)

        async def watch_exit():     # uvicorn owns the signal handlers; mirror its flag
            while not server.should_exit:
                await asyncio.sleep(0.5)
            stop.set()

        tasks.append(asyncio.create_task(watch_exit(), name="watch-exit"))

    if not tasks:
        log.error("Nothing to run (both Telegram and web disabled).")
        return 1

    # Warm the cache in the background so the first request is instant
    async def warm():
        try:
            await cache.refresh(with_briefing=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("Initial analysis failed: %s", exc)

    tasks.append(asyncio.create_task(warm(), name="warm"))

    # Free-tier hosts spin the instance down after ~15 idle minutes, which stops the
    # news scans and alerts. A periodic request to our own public URL keeps it warm.
    if settings.keepalive and settings.keepalive_url and not args.no_web:
        import httpx

        async def keepalive():
            url = settings.keepalive_url.rstrip("/") + "/api/health"
            interval = max(60, settings.keepalive_interval_min * 60)
            log.info("Keep-alive: pinging %s every %d s", url, interval)
            async with httpx.AsyncClient(timeout=20) as client:
                while True:
                    await asyncio.sleep(interval)
                    try:
                        await client.get(url)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("keep-alive ping failed: %s", exc)

        tasks.append(asyncio.create_task(keepalive(), name="keepalive"))

    # Optional self-check: replay the last weeks of bars through the signal rules (see signals/backtest.py)
    if settings.backtest_on_start and not args.demo:
        from signals.backtest import run_on_start

        tasks.append(asyncio.create_task(run_on_start(cache), name="backtest"))

    # Run until something sets ``stop`` (SIGTERM from the host on redeploy, Ctrl-C locally), then shut down
    # in order: last backup snapshot, stop Telegram polling, stop the tick streams, exit promptly.
    runner = asyncio.gather(*tasks)                 # finishes when every component ends – or one of them fails
    stopper = asyncio.create_task(stop.wait(), name="stop")
    code = 0
    try:
        done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if runner in done and not runner.cancelled() and runner.exception() is not None:
            log.error("A component failed: %r – restarting", runner.exception())
            code = 1
    except asyncio.CancelledError:
        pass
    log.info("Shutting down…")
    stopper.cancel()
    if backup.enabled:
        await asyncio.to_thread(backup.snapshot)
    web_task = next((t for t in tasks if t.get_name() == "web"), None)
    if web_task is not None and not web_task.done():     # let uvicorn finish its own graceful shutdown first
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(web_task), timeout=6)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=12)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    if not runner.done():
        runner.cancel()
    try:
        await runner
    except BaseException:  # noqa: BLE001 – the gathered components were cancelled on purpose
        pass
    if engine is not None:
        await engine.stop()
    log.info("Bye")
    return code


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)

    problems = settings.validate()
    if not args.demo:
        for p in problems:
            if "TELEGRAM_BOT_TOKEN" in p and (args.no_telegram or args.once):
                continue
            log.warning("Config: %s", p)

    try:
        code = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
