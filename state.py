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
                                     pending_max_hours=st.signal_pending_max_hours, require_trigger=st.signal_require_trigger)
        self._report: Report | None = None
        self._lock = threading.Lock()
        self._history: list[dict] = []   # recent signals (for the dashboard sparkline)
        self.last_error: str | None = None

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

    def refresh_sync(self, with_briefing: bool = True, refresh_news: bool = True) -> Report:
        """Run the pipeline (blocking). Only one refresh runs at a time; callers that queued up behind a
        refresh that just finished get that fresh report instead of running the pipeline again."""
        asked_at = datetime.now(timezone.utc)
        with self._lock:
            rep = self._report
            if rep is not None and rep.generated_at >= asked_at - timedelta(seconds=20) \
                    and (not with_briefing or rep.briefing is not None or not self.pipeline.settings.ai_enabled):
                return rep
            try:
                report = self.pipeline.run(with_briefing=with_briefing, refresh_news=refresh_news)
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.exception("Pipeline run failed")
                raise
            if report.briefing is None and rep is not None and rep.briefing and not with_briefing \
                    and (report.generated_at - rep.generated_at) < timedelta(minutes=max(30, self.max_age_sec // 60)):
                report.briefing = rep.briefing          # a light refresh keeps the last written briefing
            self._report = report
            try:
                self.tracker.on_analysis(report)
            except Exception:  # noqa: BLE001
                log.exception("Signal tracker failed")
            self._history.append(report.signal.to_dict())
            self._history = self._history[-200:]
            return report

    async def refresh(self, with_briefing: bool = True, refresh_news: bool = True) -> Report:
        return await asyncio.to_thread(self.refresh_sync, with_briefing, refresh_news)

    async def get(self, max_age_sec: int | None = None, with_briefing: bool = True) -> Report:
        """Return a fresh-enough report, refreshing if needed."""
        limit = self.max_age_sec if max_age_sec is None else max_age_sec
        rep = self._report
        if rep is not None and (datetime.now(timezone.utc) - rep.generated_at).total_seconds() <= limit:
            if not with_briefing or rep.briefing is not None or not self.pipeline.settings.ai_enabled:
                return rep
        return await self.refresh(with_briefing=with_briefing)
