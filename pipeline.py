"""
The analysis pipeline: price data → technicals → news → sentiment → signal
→ (optional) AI briefing.  Everything here is synchronous; the Telegram
layer runs it in a worker thread.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ai.analyst import write_briefing
from config import Settings
from market.analysis import TechSnapshot, analyze_technicals
from market.data import MarketData, fetch_market_data
from news.calendar import CalendarService, Event
from news.fetch import fetch_all
from news.sentiment import ClaudeClassifier, NewsScore, aggregate, apply_lexicon, is_high_impact
from news.store import Article, Store
from signals.engine import Signal, build_signal
from signals.session import SessionInfo, session_info

log = logging.getLogger(__name__)


@dataclass
class Report:
    data: MarketData
    tech: TechSnapshot
    news: NewsScore
    signal: Signal
    briefing: str | None
    generated_at: datetime
    events: list[Event] = field(default_factory=list)   # upcoming calendar events (48h)
    session: SessionInfo | None = None                  # trading-session verdict at generation time
    briefing_at: datetime | None = None                 # when the briefing text was written

    @property
    def next_event(self) -> Event | None:
        for e in self.events:
            if e.impact == "High" and e.minutes_away > -15:
                return e
        return None


class Pipeline:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.calendar = CalendarService(settings.calendar_url, settings.calendar_currencies)
        self._classifier: ClaudeClassifier | None = None
        if settings.ai_enabled:
            try:
                self._classifier = ClaudeClassifier(settings.anthropic_api_key, settings.classifier_model)
                log.info("AI classification enabled (%s)", settings.classifier_model)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not initialise Claude classifier: %s", exc)

    # ---- news --------------------------------------------------------------
    def scan_news(self) -> list[Article]:
        """Fetch feeds, store new articles, score them. Returns the NEW articles."""
        fetched = fetch_all(
            self.settings.feeds,
            max_age_hours=self.settings.news_lookback_hours * 2,
            workers=self.settings.feed_workers,
            timeout=self.settings.feed_timeout_sec,
        )
        new = [a for a in fetched if not self.store.has_article(a.id)]
        if new:
            self._score(new)
            for a in new:
                a.high_impact = is_high_impact(a, self.settings.high_impact_terms)
            self.store.upsert_articles(new)
            log.info("Stored %d new articles (%d high impact)", len(new), sum(a.high_impact for a in new))
        # Score anything that was stored but never scored (e.g. after an API outage)
        pending = self.store.unscored_articles()
        if pending:
            self._score(pending)
            self.store.update_scores(pending)
        self.store.prune(days=14)
        return new

    def _score(self, articles: list[Article]) -> None:
        if self._classifier:
            self._classifier.score(articles)
        else:
            apply_lexicon(articles)

    def news_score(self) -> NewsScore:
        recent = self.store.recent_articles(self.settings.news_lookback_hours)
        return aggregate(recent, half_life_hours=self.settings.news_half_life_hours)

    _last_read: str = ""

    def _log_read(self, tech, signal) -> None:
        """One line per *change* of the chart read, so the log shows what the engine sees without spamming."""
        pa = tech.pa
        if pa is None:
            line = f"Read: tech {tech.score:+d} · {signal.bias} {signal.confidence}% · no price-action read"
        else:
            zone = (f" · zone {signal.zone_lo:,.1f}–{signal.zone_hi:,.1f} [{' · '.join(signal.confluence) or 'no confluence'}]"
                    if signal.zone_lo is not None else "")
            m = getattr(tech, "macro", None)
            macro = f" · macro {m.get('label')} {m.get('score', 0):+d}" if m else " · macro n/a"
            line = (f"Read: {tech.price:,.1f} · tech {tech.score:+d} · {signal.bias} {signal.confidence}% · H4 {pa.h4.trend} ({pa.h4.label}) · "
                    f"H1 {pa.h1.trend} ({pa.h1.label}){' · BOS ' + pa.h1.bos if pa.h1.bos else ''}{' · CHoCH ' + pa.h1.choch if pa.h1.choch else ''} · "
                    f"{pa.regime} ADX {pa.adx:.0f} · ATR {pa.atr_pct:.0f}pct{' · ' + pa.divergence + ' div' if pa.divergence else ''}"
                    f"{' · ' + pa.candle if pa.candle else ''}{macro}{zone}")
        if line != self._last_read:
            log.info(line)
            self._last_read = line

    # ---- full report -------------------------------------------------------
    def run(self, with_briefing: bool = True, refresh_news: bool = True, bars_max_age_sec: float = 0.0) -> Report:
        if self.settings.account_size and self.settings.account_currency != "USD":
            try:
                from market.fx import usd_rate
                usd_rate(self.settings.account_currency)      # refresh the hourly cache off the event loop
            except Exception as exc:  # noqa: BLE001
                log.debug("fx refresh failed: %s", exc)
        data = fetch_market_data(max_age_sec=bars_max_age_sec)
        tech = analyze_technicals(data)
        if refresh_news:
            try:
                self.scan_news()
            except Exception as exc:  # noqa: BLE001
                log.warning("News scan failed: %s", exc)
        news = self.news_score()
        try:
            events = self.calendar.upcoming(hours=48)
        except Exception as exc:  # noqa: BLE001
            log.warning("Calendar failed: %s", exc)
            events = []
        session = None
        if self.settings.session_filter:
            session = session_info(events=events, asia_entries=self.settings.asia_entries,
                                   blackout_before_min=self.settings.news_blackout_before_min,
                                   blackout_after_min=self.settings.news_blackout_after_min,
                                   blocked=self.settings.blocked_sessions or None)
        signal = build_signal(
            tech,
            news,
            tech_weight=self.settings.tech_weight,
            news_weight=self.settings.news_weight,
            atr_stop_mult=self.settings.atr_stop_mult,
            atr_target_mult=self.settings.atr_target_mult,
            session=session,
            zone_refine=self.settings.zone_refine,
        )
        self._log_read(tech, signal)
        briefing = None
        briefing_at = None
        if with_briefing and self.settings.ai_enabled:
            briefing = write_briefing(self.settings.anthropic_api_key, self.settings.analyst_model, tech, news, events)
            briefing_at = datetime.now(timezone.utc) if briefing else None
        return Report(
            data=data,
            tech=tech,
            news=news,
            signal=signal,
            briefing=briefing,
            generated_at=datetime.now(timezone.utc),
            events=events,
            session=session,
            briefing_at=briefing_at,
        )
