"""
Demo mode: a pipeline that uses synthetic prices and a fixed set of sample
headlines, so you can open the dashboard (and poke the bot) without any API
keys or internet access.  Start with:  python main.py --demo
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from market.synthetic import make_market
from news.fetch import article_id
from news.sentiment import apply_lexicon, is_high_impact
from news.store import Article
from pipeline import Pipeline

log = logging.getLogger(__name__)

SAMPLE_HEADLINES = [
    ("Gold hits fresh record high as dollar slides on growing Fed rate-cut bets", "Reuters", 1.5),
    ("US CPI cools more than expected, boosting rate cut hopes", "CNBC", 3.0),
    ("Central banks extend gold buying streak as reserve diversification continues", "Kitco", 5.5),
    ("Treasury yields tick higher after strong retail sales data", "Bloomberg", 7.0),
    ("Middle East tensions escalate; safe-haven demand lifts gold and the yen", "FXStreet", 9.0),
    ("Profit-taking pulls gold back from record; ETF outflows noted", "Investing.com", 12.0),
    ("Fed's Powell says policy is well positioned, no rush to cut", "WSJ", 16.0),
    ("Dollar index steadies ahead of payrolls; traders eye gold at key support", "ForexLive", 20.0),
    ("China's PBOC adds gold to reserves for an 11th straight month", "Reuters", 26.0),
    ("Analysts raise year-end gold forecast citing real yields and geopolitics", "Kitco", 30.0),
]


class DemoPipeline(Pipeline):
    def __init__(self, settings, store):
        super().__init__(settings, store)
        self._classifier = None  # never call the API in demo mode
        self._seed = 9
        self.calendar.set_demo()
        from signals.tracker import SignalTracker
        SignalTracker(store).seed_demo()

    def scan_news(self) -> list[Article]:
        now = datetime.now(timezone.utc)
        arts: list[Article] = []
        for title, source, age_h in SAMPLE_HEADLINES:
            a = Article(
                id=article_id(title),
                title=title,
                link="https://example.com/" + article_id(title),
                source=source,
                summary="",
                published_at=now - timedelta(hours=age_h),
            )
            if not self.store.has_article(a.id):
                arts.append(a)
        if arts:
            apply_lexicon(arts)
            for a in arts:
                a.high_impact = is_high_impact(a, self.settings.high_impact_terms)
            self.store.upsert_articles(arts)
        return arts

    def run(self, with_briefing: bool = True, refresh_news: bool = True, bars_max_age_sec: float = 0.0):
        import pipeline as _p

        original = _p.fetch_market_data
        _p.fetch_market_data = lambda max_age_sec=0.0: make_market(drift_h=0.004, seed=self._seed, start=3600.0)
        try:
            report = super().run(with_briefing=False, refresh_news=refresh_news)
        finally:
            _p.fetch_market_data = original
        report.briefing = self._demo_briefing(report)
        return report

    @staticmethod
    def _demo_briefing(report) -> str:
        sig, t, n = report.signal, report.tech, report.news
        agree = {
            "aligned": "technicals and news agree",
            "conflict": "technicals and news disagree, so expect choppy price action",
            "one-sided": "the signal is carried mostly by one side",
        }[sig.agreement]
        themes = ", ".join(th for th, _ in n.top_themes[:3]) or "quiet news flow"
        res = f"{sig.nearest_resistance:,.0f}" if sig.nearest_resistance else "the recent high"
        sup = f"{sig.nearest_support:,.0f}" if sig.nearest_support else "the recent low"
        return (
            f"(Demo briefing – generated from the numbers, not by the AI.) Bias: {sig.label.lower()} "
            f"with {sig.confidence}% confidence; {agree}. Technical score {t.score:+d} ({t.trend_label.lower()}), "
            f"news score {n.score:+d} driven by {themes}. Watch {res} above and {sup} below; "
            f"ATR is {t.atr:.0f}, so give levels room. Risk: a hawkish surprise or a dollar bounce would "
            f"invalidate the bullish news backdrop quickly."
        )
