"""
Shared in-memory state: the latest report, refreshed by the scheduler and
read by both the Telegram bot and the web dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

from pipeline import Pipeline, Report
from signals.tracker import SignalTracker

log = logging.getLogger(__name__)


class ReportCache:
    def __init__(self, pipeline: Pipeline, max_age_sec: int = 300):
        self.pipeline = pipeline
        self.max_age_sec = max_age_sec
        st = pipeline.settings
        self.tracker = SignalTracker(pipeline.store, min_conf=st.signal_min_conf, max_hours=st.signal_max_hours,
                                     breakeven_after_tp1=st.signal_breakeven_after_tp1,
                                     pending_max_hours=st.signal_pending_max_hours, require_trigger=st.signal_require_trigger,
                                     spread_usd=st.spread_usd, slippage_usd=st.slippage_usd,
                                     trigger_kinds=st.trigger_kinds or None)
        from bot import formatting as _fmt

        _fmt.BREAKEVEN_AFTER_TP1 = bool(st.signal_breakeven_after_tp1)     # the call wording follows the management rule
        _fmt.MIN_CONF = int(st.signal_min_conf)
        self._report: Report | None = None
        self._lock = threading.Lock()
        self._history: list[dict] = []   # recent signals (for the dashboard sparkline)
        self.last_error: str | None = None
        self.backup = None               # StateBackup when REDIS_URL is set (see backup.py)

    @property
    def report(self) -> Report | None:
        return self._report

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    def is_stale(self) -> bool:
        if self._report is None:
            return True
        return (datetime.now(timezone.utc) - self._report.generated_at).total_seconds() > self.max_age_sec

    def refresh_sync(self, with_briefing: bool = True, refresh_news: bool = True, fast: bool = False) -> Report:
        """Run the pipeline (blocking). Only one refresh runs at a time; callers that queued up behind a
        refresh that just finished get that fresh report instead of running the pipeline again.
        ``fast`` reuses the provider's bars for up to 150 s (the live tick bars are always fresh)."""
        asked_at = datetime.now(timezone.utc)
        st = self.pipeline.settings
        with self._lock:
            rep = self._report
            if rep is not None and rep.generated_at >= asked_at - timedelta(seconds=20 if not fast else 5) \
                    and (not with_briefing or rep.briefing is not None or not st.ai_enabled):
                return rep
            # A fresh briefing costs API tokens: write one at most every BRIEFING_INTERVAL_MIN unless the bias changed.
            carry = None
            if with_briefing and rep is not None and rep.briefing and rep.briefing_at is not None \
                    and asked_at - rep.briefing_at < timedelta(minutes=max(1, st.briefing_interval_min)):
                carry = rep
                with_briefing = False
            try:
                report = self.pipeline.run(with_briefing=with_briefing, refresh_news=refresh_news,
                                           bars_max_age_sec=150.0 if fast else 0.0)
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.exception("Pipeline run failed")
                raise
            if carry is not None and report.signal.bias != carry.signal.bias and st.ai_enabled:
                try:                                    # the picture changed: a new briefing is worth its tokens
                    from ai.analyst import write_briefing
                    report.briefing = write_briefing(st.anthropic_api_key, st.analyst_model, report.tech, report.news, report.events)
                    report.briefing_at = datetime.now(timezone.utc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Briefing failed: %s", exc)
            if report.briefing is None and rep is not None and rep.briefing \
                    and (report.generated_at - rep.generated_at) < timedelta(hours=3):
                report.briefing, report.briefing_at = rep.briefing, rep.briefing_at   # keep the last written briefing
            self._report = report
            try:
                self.tracker.on_analysis(report)
            except Exception:  # noqa: BLE001
                log.exception("Signal tracker failed")
            self._history.append(report.signal.to_dict())
            self._history = self._history[-200:]
            return report

    async def refresh(self, with_briefing: bool = True, refresh_news: bool = True, fast: bool = False) -> Report:
        return await asyncio.to_thread(self.refresh_sync, with_briefing, refresh_news, fast)

    async def get(self, max_age_sec: int | None = None, with_briefing: bool = True) -> Report:
        """Return a fresh-enough report, refreshing if needed."""
        limit = self.max_age_sec if max_age_sec is None else max_age_sec
        rep = self._report
        if rep is not None and (datetime.now(timezone.utc) - rep.generated_at).total_seconds() <= limit:
            if not with_briefing or rep.briefing is not None or not self.pipeline.settings.ai_enabled:
                return rep
        return await self.refresh(with_briefing=with_briefing)
