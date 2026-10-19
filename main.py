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
    from news.store import Store
    from state import ReportCache

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

    if not args.no_telegram:
        if not settings.telegram_token:
            log.error("TELEGRAM_BOT_TOKEN is not set – starting without Telegram (use --no-telegram to silence this)")
        else:
            from bot.telegram_bot import SentinelBot

            bot = SentinelBot(settings, store, cache)
            tasks.append(asyncio.create_task(bot.run_forever(), name="telegram"))

    if not args.no_web:
        import uvicorn

        from web.server import create_app

        app = create_app(cache, demo=args.demo)
        config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", loop="asyncio")
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve(), name="web"))
        log.info("Dashboard: http://localhost:%d", args.port)

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

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    return 0


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
