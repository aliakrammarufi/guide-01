"""
Telegram bot: commands + scheduled jobs.

Commands: /start /help /price /signal /analyze /news /levels /subscribe
          /unsubscribe /alerts /status
Jobs:     scheduled full report (every ANALYSIS_INTERVAL_MIN)
          monitor (every NEWS_SCAN_INTERVAL_MIN): news scan, high-impact
          news alerts, big-move alerts, bias-change alerts
"""
from __future__ import annotations

import asyncio
import html
import re
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from html import escape

from telegram import BotCommand, ForceReply, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from bot import formatting as fmt
from bot.chart import render_chart
from config import Settings
from news.store import Store
from signals.engine import bias_changed
from signals.sizing import size_for
from state import ReportCache

log = logging.getLogger(__name__)

TG_LIMIT = 4000  # keep under Telegram's 4096-char message limit


class SentinelBot:
    def __init__(self, settings: Settings, store: Store, cache: ReportCache):
        self.settings = settings
        self.store = store
        self.cache = cache
        self.app: Application = ApplicationBuilder().token(settings.telegram_token).build()
        self._awaiting_risk: set[tuple[int, int]] = set()     # (chat id, user id) waiting to answer /risk
        self._flush_lock = asyncio.Lock()
        self._register()

    # ------------------------------------------------------------------ setup
    def _register(self) -> None:
        handlers = {
            "start": self.cmd_start,
            "help": self.cmd_help,
            "price": self.cmd_price,
            "signal": self.cmd_signal,
            "analyze": self.cmd_analyze,
            "analyse": self.cmd_analyze,
            "news": self.cmd_news,
            "levels": self.cmd_levels,
            "calendar": self.cmd_calendar,
            "signals": self.cmd_signals,
            "subscribe": self.cmd_subscribe,
            "unsubscribe": self.cmd_unsubscribe,
            "alerts": self.cmd_alerts,
            "risk": self.cmd_risk,
            "backtest": self.cmd_backtest,
            "selfcheck": self.cmd_backtest,
            "took": self.cmd_took,
            "skipped": self.cmd_skipped,
            "scorecard": self.cmd_scorecard,
            "status": self.cmd_status,
        }
        for name, func in handlers.items():
            self.app.add_handler(CommandHandler(name, self._guard(func)))
        # plain text: answers to the /risk question (replies to the bot work in groups too)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._guard(self.on_text)))
        self.app.add_error_handler(self._on_error)

        jq = self.app.job_queue
        grace = {"misfire_grace_time": 120}   # a run that starts late (busy startup) still runs instead of being skipped
        jq.run_repeating(self.job_report, interval=timedelta(minutes=self.settings.analysis_interval_min), first=timedelta(seconds=20), name="report", job_kwargs=grace)
        jq.run_repeating(self.job_monitor, interval=timedelta(minutes=self.settings.news_scan_interval_min), first=timedelta(seconds=90), name="monitor", job_kwargs=grace)
        # weekly scorecard: Monday 06:30 UTC (before London), to every subscriber
        from datetime import time as _time
        jq.run_daily(self.job_scorecard, time=_time(6, 30, tzinfo=timezone.utc), days=(0,), name="scorecard", job_kwargs=grace)

    async def _post_init(self, app: Application) -> None:
        await app.bot.set_my_commands(
            [
                BotCommand("price", "Current XAU/USD price"),
                BotCommand("signal", "Bias, confidence, levels"),
                BotCommand("analyze", "Full report with chart"),
                BotCommand("news", "Gold-moving headlines"),
                BotCommand("levels", "Support / resistance / pivots"),
                BotCommand("signals", "BUY/SELL call with SL, TP1, TP2"),
                BotCommand("calendar", "Upcoming CPI / FOMC / payrolls"),
                BotCommand("subscribe", "Get scheduled reports here"),
                BotCommand("unsubscribe", "Stop scheduled reports"),
                BotCommand("alerts", "Alerts on|off"),
                BotCommand("risk", "Account size & % risk → lot size per call"),
                BotCommand("backtest", "How the rules did on recent real bars"),
                BotCommand("took", "Mark the latest call as taken"),
                BotCommand("skipped", "Mark the latest call as skipped"),
                BotCommand("scorecard", "This week's results"),
                BotCommand("status", "Bot status"),
                BotCommand("help", "Commands"),
            ]
        )
        log.info("Telegram bot ready")

    def _guard(self, func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat = update.effective_chat
            if chat is None:
                return
            if self.settings.allowed_chat_ids and chat.id not in self.settings.allowed_chat_ids:
                where = "This group's" if chat.type in ("group", "supergroup") else "Your"
                await context.bot.send_message(
                    chat.id,
                    f"🔒 <b>PRIVATE BOT</b>\n{where} chat id is <code>{chat.id}</code>.\n"
                    f"The owner can allow it by adding it to <code>TELEGRAM_ALLOWED_CHAT_IDS</code> on the server.",
                    parse_mode=ParseMode.HTML,
                )
                return
            try:
                await func(update, context)
            except Exception as exc:  # noqa: BLE001
                log.exception("Command failed")
                await context.bot.send_message(chat.id, f"⚠️ Something went wrong: <code>{escape(str(exc)[:300])}</code>", parse_mode=ParseMode.HTML)
        return wrapper

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Telegram error: %s", context.error)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _units(text: str) -> list[tuple[str, str]]:
        """Break a message into (piece, separator-before-it) pairs that are each valid HTML on their
        own: paragraphs; an oversized paragraph into lines, keeping <pre> blocks whole."""
        units: list[tuple[str, str]] = []
        for para in text.split("\n\n"):
            if len(para) <= TG_LIMIT:
                units.append((para, "\n\n"))
                continue
            buf: list[str] = []
            in_pre = False
            first = True
            for line in para.split("\n"):
                buf.append(line)
                if "<pre>" in line and "</pre>" not in line:
                    in_pre = True
                if "</pre>" in line:
                    in_pre = False
                if not in_pre:
                    units.append(("\n".join(buf), "\n\n" if first else "\n"))
                    buf = []
                    first = False
            if buf:
                units.append(("\n".join(buf), "\n"))
        return units

    async def _send(self, chat_id: int, text: str, **kwargs) -> None:
        """Send HTML text; long messages are split only at paragraph / line boundaries so
        every chunk keeps its tags balanced (Telegram rejects a chunk cut inside a tag)."""
        chunks: list[str] = []
        current = ""
        for unit, sep in self._units(text):
            candidate = f"{current}{sep}{unit}" if current else unit
            if len(candidate) > TG_LIMIT and current:
                chunks.append(current)
                current = unit
            else:
                current = candidate
        if current:
            chunks.append(current)
        for chunk in chunks:
            try:
                await self.app.bot.send_message(chat_id, chunk[:4096], parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kwargs)
            except TelegramError as exc:
                if "parse entities" in str(exc).lower():
                    log.warning("HTML rejected, sending plain text: %s", exc)
                    await self.app.bot.send_message(chat_id, html.unescape(re.sub(r"<[^>]+>", "", chunk))[:4096], disable_web_page_preview=True, **kwargs)
                else:
                    raise

    async def _send_chart(self, chat_id: int, report) -> None:
        if not self.settings.send_charts:
            return
        try:
            png = await asyncio.to_thread(render_chart, report.data.hourly, report.tech, report.signal)
            await self.app.bot.send_photo(chat_id, png, caption=f"XAU/USD H1 · {report.signal.label}")
        except Exception as exc:  # noqa: BLE001
            log.warning("Chart failed: %s", exc)

    async def _broadcast(self, chat_ids: list[int], text: str, report=None, with_chart: bool = False) -> None:
        for chat_id in chat_ids:
            try:
                if with_chart and report is not None:
                    await self._send_chart(chat_id, report)
                await self._send(chat_id, text)
            except Forbidden:
                log.info("Chat %s blocked the bot – unsubscribing", chat_id)
                self.store.unsubscribe(chat_id)
            except TelegramError as exc:
                log.warning("Send to %s failed: %s", chat_id, exc)

    async def _typing(self, update: Update) -> None:
        try:
            await update.effective_chat.send_action(ChatAction.TYPING)
        except TelegramError:
            pass

    # --------------------------------------------------------------- sizing
    def _risk_settings(self, chat_id: int) -> tuple[float | None, float | None]:
        d = self.store.get_state(f"risk:{chat_id}") or {}
        acct, pct = d.get("account"), d.get("risk_pct")
        if not acct and self.settings.account_size:
            return self.settings.account_size, self.settings.risk_pct
        return (float(acct) if acct else None, float(pct) if pct else None)

    def _fixed_lots(self, chat_id: int) -> float:
        d = self.store.get_state(f"risk:{chat_id}") or {}
        if "fixed_lots" in d:
            return float(d.get("fixed_lots") or 0.0)
        return self.settings.fixed_lots

    def _sizing(self, chat_id: int, entry: float, stop: float):
        acct, pct = self._risk_settings(chat_id)
        if not acct:
            return None
        from market.fx import usd_rate
        fx, _ = usd_rate(self.settings.account_currency, cached_only=True)
        return size_for(acct, pct or 1.0, entry, stop, self.settings.contract_oz,
                        currency=self.settings.account_currency, fx=fx, fixed_lots=self._fixed_lots(chat_id))

    RISK_PROMPT = "Reply with your account size and the % you risk per trade"

    @staticmethod
    def _parse_risk(text: str) -> tuple[float, float] | None:
        """'5000 1', '$5,000 1%', '5000, 1', '5000' → (account, pct). None if it doesn't look like that."""
        nums = re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")
        if not nums or len(nums) > 2:
            return None
        try:
            account = float(nums[0].replace(",", ""))
            pct = float(nums[1].replace(",", "")) if len(nums) > 1 else 1.0
        except ValueError:
            return None
        if account < 10 or not (0 < pct <= 10):
            return None
        return account, pct

    def _risk_summary(self, chat_id: int) -> str:
        acct, pct = self._risk_settings(chat_id)
        return fmt.format_risk_settings(acct, pct, currency=self.settings.account_currency, fixed_lots=self._fixed_lots(chat_id))

    async def _save_risk(self, chat_id: int, account: float, pct: float, user_id: int | None = None) -> None:
        d = self.store.get_state(f"risk:{chat_id}") or {}
        d.update({"account": account, "risk_pct": pct})
        self.store.set_state(f"risk:{chat_id}", d)
        self._awaiting_risk.discard((chat_id, user_id))
        await self._send(chat_id, "✅ <b>SAVED</b>\n\n" + self._risk_summary(chat_id))

    async def _ask_risk(self, update: Update) -> None:
        chat_id = update.effective_chat.id
        uid = update.effective_user.id if update.effective_user else None
        self._awaiting_risk.add((chat_id, uid))
        acct, _ = self._risk_settings(chat_id)
        current = self._risk_summary(chat_id) if acct else "💼 <b>POSITION SIZING</b> — not set yet"
        try:
            await update.effective_message.reply_text(
                f"{current}\n\n✍️ {self.RISK_PROMPT}, for example <code>5000 1</code> "
                f"($5,000 account, 1% risk = $50 per trade). Send <code>off</code> to turn it off.",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True, input_field_placeholder="account size  risk %   e.g. 5000 1"),
            )
        except TelegramError as exc:
            log.warning("risk prompt failed: %s", exc)

    async def cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        uid = update.effective_user.id if update.effective_user else None
        raw = " ".join(context.args or []).strip()
        if raw.lower() in {"off", "stop", "none"}:
            self.store.set_state(f"risk:{chat_id}", {})
            self._awaiting_risk.discard((chat_id, uid))
            await self._send(chat_id, "💼 <b>POSITION SIZING OFF</b>")
            return
        if raw.lower().startswith("lots"):
            m = re.search(r"\d+(?:\.\d+)?", raw)
            lots = float(m.group(0)) if m else 0.0
            d = self.store.get_state(f"risk:{chat_id}") or {}
            d["fixed_lots"] = lots if 0 < lots <= 10 else 0.0
            self.store.set_state(f"risk:{chat_id}", d)
            await self._send(chat_id, ("✅ <b>SAVED</b> — fixed size " + f"<b>{lots:.2f} lots</b>" if d["fixed_lots"] else "✅ <b>SAVED</b> — sizing by risk % again") + "\n\n" + self._risk_summary(chat_id))
            return
        if raw:
            parsed = self._parse_risk(raw)
            if parsed:
                await self._save_risk(chat_id, *parsed, user_id=uid)
            else:
                await self._send(chat_id, "I couldn't read that. Send it as <code>/risk 5000 1</code> — account size, then % risk per trade (max 10%). "
                                          "<code>/risk lots 0.05</code> fixes your lot size; <code>/risk lots 0</code> goes back to sizing by risk.")
            return
        await self._ask_risk(update)

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Plain text is only ever an answer to the /risk question (asked by this user, or a reply to the prompt)."""
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        chat_id = update.effective_chat.id
        uid = update.effective_user.id if update.effective_user else None
        replied = msg.reply_to_message
        answering = (chat_id, uid) in self._awaiting_risk or (
            replied is not None and replied.from_user is not None and replied.from_user.id == context.bot.id
            and self.RISK_PROMPT.lower() in (replied.text or "").lower()
        )
        if not answering:
            return                      # never interpret ordinary chat ("what about 4150?") as account settings
        if msg.text.strip().lower() in {"off", "stop", "none"}:
            self.store.set_state(f"risk:{chat_id}", {})
            self._awaiting_risk.discard((chat_id, uid))
            await self._send(chat_id, "💼 <b>POSITION SIZING OFF</b>")
            return
        parsed = self._parse_risk(msg.text)
        if parsed:
            await self._save_risk(chat_id, *parsed, user_id=uid)
        else:
            await self._ask_risk(update)

    # -------------------------------------------------------------- commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self.store.subscribe(chat_id)
        private = bool(self.settings.allowed_chat_ids)
        lock = ("🔒 This bot is private to your chat." if private else
                f"🔓 Your chat id is <code>{chat_id}</code> — set <code>TELEGRAM_ALLOWED_CHAT_IDS={chat_id}</code> on the server to make the bot private.")
        await self._send(
            chat_id,
            "🥇 <b>XAU SENTINEL — ONLINE</b>\n"
            "Gold (XAU/USD) analysis, news and trade setups.\n\n"
            f"✅ Subscribed — a report every <b>{self.settings.analysis_interval_min} min</b> plus alerts: bias changes, big moves, "
            "breaking news, pre-release warnings, and every BUY / SELL setup and live call.\n"
            f"{lock}\n\n"
            "Start with /price or /signal. Set /risk for lot sizes.\n\n" + fmt.HELP,
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send(update.effective_chat.id, fmt.HELP)

    async def cmd_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._typing(update)
        rep = await self.cache.get(max_age_sec=120, with_briefing=False)
        await self._send(update.effective_chat.id, fmt.format_price(rep.tech, rep.data.source, rep.data.last_bar_time))

    async def cmd_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._typing(update)
        rep = await self.cache.get(with_briefing=False)
        await self._send(update.effective_chat.id, fmt.format_signal_message(rep.tech, rep.signal, rep.data.source, rep.data.last_bar_time))

    async def cmd_analyze(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        await self._typing(update)
        await self._send(chat_id, "⏳ <i>Running the full analysis…</i>")
        rep = await self.cache.get(with_briefing=True)
        await self._send_chart(chat_id, rep)
        await self._send(chat_id, self._full_report(rep, chat_id))

    def _full_report(self, rep, chat_id: int | None = None) -> str:
        trade = self.cache.tracker.live()
        sizing = self._sizing(chat_id, trade.entry, trade.sl) if (trade is not None and chat_id is not None) else None
        return fmt.format_full_report(
            rep.tech, rep.news, rep.signal, rep.briefing, rep.data.source, rep.data.last_bar_time,
            max_headlines=self.settings.max_headlines_in_report, hours=self.settings.news_lookback_hours,
            events=rep.events, trade=trade, sizing=sizing,
        )

    async def cmd_signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._typing(update)
        rep = await self.cache.get(with_briefing=False)
        tr = self.cache.tracker
        chat_id = update.effective_chat.id
        marks = self.store.marks(chat_id)
        mine = tr.stats(only_ids={i for i, m in marks.items() if m == "took"}) if marks else None
        text = fmt.format_signals_overview(tr.live(), tr.history(limit=12), tr.stats(), rep.tech.price,
                                           sizing_for=lambda entry, stop: self._sizing(chat_id, entry, stop),
                                           session=rep.session.to_dict() if rep.session else None, mine=mine)
        await self._send(chat_id, text)

    # ---- /took /skipped /scorecard -------------------------------------------
    def _mark_target(self, args: list[str]):
        """The call to mark: an explicit #id, else the live call, else the most recent traded call of the last 48 h."""
        tr = self.cache.tracker
        if args:
            try:
                wanted = int(args[0].lstrip("#"))
                return next((s for s in tr.history(limit=200) if s.id == wanted), None)
            except ValueError:
                return None
        live = tr.live()
        if live is not None:
            return live
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        for s in tr.history(limit=50):
            if s.traded and (s.closed_at or s.opened_at) >= cutoff:
                return s
        return None

    async def _mark(self, update: Update, context: ContextTypes.DEFAULT_TYPE, mark: str) -> None:
        chat_id = update.effective_chat.id
        sig = self._mark_target(list(context.args or []))
        if sig is None:
            await self._send(chat_id, "No recent call to mark. Use <code>/took 12</code> with the call number from /signals.")
            return
        user = update.effective_user.id if update.effective_user else None
        self.store.mark_trade(sig.id, chat_id, user, mark)
        await self._send(chat_id, fmt.format_mark(sig, mark))

    async def cmd_took(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._mark(update, context, "took")

    async def cmd_skipped(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._mark(update, context, "skipped")

    def _scorecard_text(self, chat_id: int) -> str:
        tr = self.cache.tracker
        since = datetime.now(timezone.utc) - timedelta(days=7)
        week_rows = self.store.signals_since(since)
        week = tr.stats(signals=week_rows)
        allt = tr.stats()
        marks = self.store.marks(chat_id)
        mine = tr.stats(only_ids={i for i, m in marks.items() if m == "took"}) if marks else None
        bt = self.store.get_state("backtest") or {}
        sc = None
        if isinstance(bt, dict) and bt.get("summary"):
            sc = dict(bt["summary"]) | {"days": (bt.get("period") or {}).get("days", 60)}
        rows = []
        for s in [x for x in week_rows if x.traded and not x.is_open][-8:]:
            res = fmt._r(s.result_r) if s.result_r is not None else "–"
            mk = "✅" if (s.result_r or 0) > 0.05 else ("❌" if (s.result_r or 0) < -0.05 else "➖")
            tag = {"took": " ·you", "skipped": " ·skip"}.get(marks.get(s.id, ""), "")
            rows.append((s.opened_at.strftime("%a %d"), s.side, f"{s.entry:,.0f} → {(s.exit_price or s.entry):,.0f}", f"{res} {mk}{tag}"))
        label = f"{since.strftime('%d %b')} → {datetime.now(timezone.utc).strftime('%d %b')}"
        return fmt.format_scorecard(week, allt, mine, sc, rows, label)

    async def cmd_scorecard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        await self._send(chat_id, await asyncio.to_thread(self._scorecard_text, chat_id))

    async def job_scorecard(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        for chat_id in self.store.subscribers():
            try:
                await self._send(chat_id, await asyncio.to_thread(self._scorecard_text, chat_id))
            except Exception as exc:  # noqa: BLE001
                log.warning("scorecard to %s failed: %s", chat_id, exc)

    async def _flush_signal_events(self) -> None:
        async with self._flush_lock:
            await self._flush_signal_events_locked()

    async def _flush_signal_events_locked(self) -> None:
        events = self.cache.tracker.drain_events()
        if not events:
            return
        subs = self.store.subscribers()          # signal calls go to every subscriber, alerts on or off
        for e in events:
            for chat_id in subs:
                sz = self._sizing(chat_id, e.signal.entry, e.signal.sl)
                await self._broadcast([chat_id], fmt.format_signal_event(e, sz))

    async def cmd_calendar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._typing(update)
        events = await asyncio.to_thread(self.cache.pipeline.calendar.upcoming, 72)
        await self._send(update.effective_chat.id, fmt.format_calendar(events, limit=12))

    async def cmd_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._typing(update)
        chat_id = update.effective_chat.id
        await asyncio.to_thread(self.cache.pipeline.scan_news)
        news = await asyncio.to_thread(self.cache.pipeline.news_score)
        await self._send(chat_id, fmt.format_news(news, max_items=12, hours=self.settings.news_lookback_hours))

    async def cmd_levels(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._typing(update)
        rep = await self.cache.get(with_briefing=False)
        await self._send(update.effective_chat.id, fmt.format_levels(rep.tech))

    async def cmd_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self.store.subscribe(chat_id)
        await self._send(chat_id, f"✅ <b>SUBSCRIBED</b>\nA report every {self.settings.analysis_interval_min} min, plus alerts. Use /alerts off to mute alerts.")

    async def cmd_unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self.store.unsubscribe(chat_id)
        await self._send(chat_id, "🔕 <b>UNSUBSCRIBED</b>\nNo more scheduled reports or alerts. Commands still work.")

    async def cmd_alerts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        arg = (context.args[0].lower() if context.args else "")
        if arg in {"on", "off"}:
            self.store.set_alerts(chat_id, arg == "on")
            await self._send(chat_id, "🔔 <b>ALERTS ON</b>" if arg == "on" else "🔕 <b>ALERTS OFF</b>")
        else:
            state = "on" if self.store.alerts_enabled(chat_id) else "off"
            await self._send(chat_id, f"🔔 Alerts are <b>{state}</b>. Use <code>/alerts on</code> or <code>/alerts off</code>.")

    async def cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        bt = await asyncio.to_thread(self.store.get_state, "backtest")
        variants = await asyncio.to_thread(self.store.get_state, "backtest_variants")
        deep = await asyncio.to_thread(self.store.get_state, "backtest_deep")
        deep_v = await asyncio.to_thread(self.store.get_state, "backtest_deep_variants")
        await self._send(update.effective_chat.id, fmt.format_backtest(bt, on_start=self.settings.backtest_on_start, variants=variants,
                                                                       deep=deep, deep_variants=deep_v))

    @staticmethod
    def _live_candles_line() -> str:
        try:
            from market import bars as live_bars
            st = live_bars.LIVE.status() if live_bars.LIVE else None
        except Exception:  # noqa: BLE001
            st = None
        if not st:
            return "off"
        age = st.get("last_tick_age_sec")
        return f"{st['bars_15m']} × 15m · {st['bars_1h']} × 1h · last tick {age:.0f}s ago" if age is not None else "waiting for ticks"

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        rep = self.cache.report
        s = self.settings
        last = rep.generated_at.strftime("%H:%M UTC · %d %b") if rep else "never"
        recent = len(self.store.recent_articles(s.news_lookback_hours))
        tr = self.cache.tracker
        live = tr.live()
        call = f"{live.side} {live.status}" if live else "none"
        ai = f"on · {s.classifier_model}" if s.ai_enabled else "off · keyword scoring"
        rows = [
            ("Last analysis", last),
            ("Report / scan", f"every {s.analysis_interval_min} / {s.news_scan_interval_min} min"),
            ("Headlines", f"{recent} in {s.news_lookback_hours}h · {len(s.feeds)} feeds"),
            ("Calendar", f"{'on' if s.calendar_url else 'off'} · warn {s.event_warn_min} min before"),
            ("Session filter", "on" if s.session_filter else "off"),
            ("Entry trigger", "15-minute confirmation" if s.signal_require_trigger else "market entry"),
            ("Min confidence", f"{s.signal_min_conf}%"),
            ("Current call", call),
            ("Closed calls", str(tr.stats()["count"])),
            ("AI", ai),
            ("Subscribers", f"{len(self.store.subscribers())} · alerts on {len(self.store.subscribers(alerts_only=True))}"),
            ("State backup", self.cache.backup.describe() if self.cache.backup else "off"),
            ("Live candles", self._live_candles_line()),
        ]
        text = "🛰 <b>STATUS</b>\n" + fmt._pre(rows, right=())
        if rep and rep.session:
            text += "\n" + fmt.session_line(rep.session.to_dict())
        if self.cache.last_error:
            text += f"\n⚠️ Last error: <code>{escape(self.cache.last_error[:200])}</code>"
        await self._send(update.effective_chat.id, text)

    # ------------------------------------------------------------------ jobs
    async def job_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        subs = self.store.subscribers()
        try:
            rep = await self.cache.refresh(with_briefing=bool(subs))
        except Exception:  # noqa: BLE001
            return
        await self._check_bias_change(rep)
        await self._flush_signal_events()
        if not subs:
            return
        for chat_id in subs:
            await self._broadcast([chat_id], self._full_report(rep, chat_id), report=rep, with_chart=True)
        log.info("Report sent to %d subscriber(s): %s %d%% · %s", len(subs), rep.signal.bias, rep.signal.confidence,
                 f"{self.cache.tracker.live().side} {self.cache.tracker.live().status}" if self.cache.tracker.live() else "no call")

    async def job_monitor(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        alert_subs = self.store.subscribers(alerts_only=True)
        # 1) news scan (also runs when nobody is subscribed, to keep the DB warm)
        try:
            await asyncio.to_thread(self.cache.pipeline.scan_news)
        except Exception as exc:  # noqa: BLE001
            log.warning("News scan failed: %s", exc)
        fresh = self.store.unalerted_high_impact(hours=6)
        if not self.store.get_state("first_scan_done"):
            # Don't replay hours of old headlines as "breaking" on first start-up
            self.store.set_state("first_scan_done", True)
        elif alert_subs and fresh:
            await self._broadcast(alert_subs, fmt.format_alert_news(fresh))
        self.store.mark_alerted([a.id for a in fresh])

        # 2) heads-up before high-impact scheduled releases (CPI, FOMC, NFP…)
        if alert_subs:
            try:
                due = await asyncio.to_thread(self.cache.pipeline.calendar.due_warnings, self.settings.event_warn_min)
            except Exception as exc:  # noqa: BLE001
                log.warning("Calendar check failed: %s", exc)
                due = []
            warned = set(self.store.get_state("warned_events", []))
            for e in due:
                if e.key in warned:
                    continue
                await self._broadcast(alert_subs, fmt.format_alert_event(e))
                warned.add(e.key)
            if due:
                self.store.set_state("warned_events", sorted(warned)[-200:])

        # 3) light price refresh (no AI briefing) → bias change + big move alerts
        try:
            rep = await self.cache.refresh(with_briefing=False, refresh_news=False)
        except Exception:  # noqa: BLE001
            return
        await self._check_bias_change(rep)
        try:
            await asyncio.to_thread(self.cache.tracker.on_price, rep.tech.price)
        except Exception as exc:  # noqa: BLE001
            log.warning("tracker on_price failed: %s", exc)
        await self._flush_signal_events()
        if alert_subs:
            move = rep.tech.change_1h_pct
            last_move_alert = self.store.get_state("last_move_alert_at")
            cooldown_ok = True
            if last_move_alert:
                cooldown_ok = datetime.now(timezone.utc) - datetime.fromisoformat(last_move_alert) > timedelta(hours=2)
            if abs(move) >= self.settings.price_move_alert_pct and cooldown_ok:
                await self._broadcast(alert_subs, fmt.format_alert_price(rep.tech, move, "hour"))
                self.store.set_state("last_move_alert_at", datetime.now(timezone.utc).isoformat())

    async def _check_bias_change(self, rep) -> None:
        prev = self.store.get_state("last_signal")
        if bias_changed(prev, rep.signal):
            if prev is not None:  # don't alert on the very first run
                alert_subs = self.store.subscribers(alerts_only=True)
                if alert_subs:
                    await self._broadcast(alert_subs, fmt.format_alert_bias_change(prev.get("bias"), rep.signal, rep.tech))
            self.store.set_state("last_signal", rep.signal.to_dict())

    # --------------------------------------------------------------- running
    async def run_forever(self) -> None:
        """Start polling and keep running until cancelled (lets us share the
        event loop with the web dashboard)."""
        async with self.app:
            await self._post_init(self.app)
            loop = asyncio.get_running_loop()

            def _on_signal_event(_ev):          # called from the tick/pipeline threads: hop onto the bot loop
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._flush_signal_events()))

            self.cache.tracker.listeners.append(_on_signal_event)
            # Allowed chats are the intended recipients: subscribe them on every start, so a
            # redeploy (which resets the free-tier database) never silently drops the reports.
            for cid in self.settings.allowed_chat_ids:
                if cid not in self.store.subscribers():
                    self.store.subscribe(cid)
                    log.info("Auto-subscribed allowed chat %s", cid)
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=True)
            log.info("Polling Telegram…")
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            finally:
                await self.app.updater.stop()
                await self.app.stop()
