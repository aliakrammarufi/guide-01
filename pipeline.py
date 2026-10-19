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

    # ---- full report -------------------------------------------------------
    def run(self, with_briefing: bool = True, refresh_news: bool = True) -> Report:
        data = fetch_market_data()
        tech = analyze_technicals(data)
        if refresh_news:
            try:
                self.scan_news()
            except Exception as exc:  # noqa: BLE001
                log.warning("News scan failed: %s", exc)
        news = self.news_score()
        signal = build_signal(
            tech,
            news,
            tech_weight=self.settings.tech_weight,
            news_weight=self.settings.news_weight,
            atr_stop_mult=self.settings.atr_stop_mult,
            atr_target_mult=self.settings.atr_target_mult,
        )
        try:
            events = self.calendar.upcoming(hours=48)
        except Exception as exc:  # noqa: BLE001
            log.warning("Calendar failed: %s", exc)
            events = []
        briefing = None
        if with_briefing and self.settings.ai_enabled:
            briefing = write_briefing(self.settings.anthropic_api_key, self.settings.analyst_model, tech, news, events)
        return Report(
            data=data,
            tech=tech,
            news=news,
            signal=signal,
            briefing=briefing,
            generated_at=datetime.now(timezone.utc),
            events=events,
        )
