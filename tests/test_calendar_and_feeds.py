from datetime import datetime, timedelta, timezone

from news import fetch as nf
from news.calendar import CalendarService, parse_events
from news.store import Article


def _iso(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


SAMPLE = [
    {"title": "CPI m/m", "country": "USD", "date": _iso(40), "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
    {"title": "Unemployment Claims", "country": "USD", "date": _iso(300), "impact": "Medium", "forecast": "225K", "previous": "219K"},
    {"title": "ECB Press Conference", "country": "EUR", "date": _iso(60), "impact": "High"},
    {"title": "Bank Holiday", "country": "USD", "date": _iso(10), "impact": "Low"},
    {"title": "FOMC Statement", "country": "USD", "date": _iso(-5), "impact": "High", "actual": "4.00%"},
    {"title": "broken", "country": "USD", "date": "not-a-date", "impact": "High"},
]


def test_parse_events_filters_and_sorts():
    ev = parse_events(SAMPLE, ["USD"])
    titles = [e.title for e in ev]
    assert titles == ["FOMC Statement", "CPI m/m", "Unemployment Claims"]   # EUR, Low and broken dropped; sorted by time
    assert ev[1].gold_weight == 4                                            # High + CPI keyword
    assert ev[1].to_dict()["forecast"] == "0.3%"


def test_due_warnings_and_next_high_impact():
    svc = CalendarService(url="", currencies=["USD"])
    svc._events = parse_events(SAMPLE, ["USD"]); svc._ts = 10 ** 12
    due = svc.due_warnings(warn_min=45)
    assert [e.title for e in due] == ["CPI m/m"]            # 40 min away, not the one 300 min away, not the past one
    assert svc.next_high_impact().title == "FOMC Statement"  # started 5 min ago → still "live" for 15 min
    assert svc.due_warnings(warn_min=30) == []


def test_calendar_without_url_is_empty():
    svc = CalendarService(url="", currencies=["USD"])
    assert svc.upcoming() == [] and svc.next_high_impact() is None


def test_fetch_all_is_concurrent_and_dedupes(monkeypatch):
    calls = []

    def fake_fetch(url, timeout=8.0):
        calls.append(url)
        t = datetime.now(timezone.utc) - timedelta(hours=1)
        a = Article(id=nf.article_id("Gold rises as dollar slips"), title="Gold rises as dollar slips", link=url, source="t", summary="", published_at=t)
        old = Article(id=nf.article_id("Ancient gold news " + url), title="Ancient gold news " + url, link=url, source="t", summary="", published_at=t - timedelta(days=10))
        return [a, old]

    monkeypatch.setattr(nf, "fetch_feed", fake_fetch)
    feeds = [f"https://feed{i}.test/rss" for i in range(12)]
    arts = nf.fetch_all(feeds, max_age_hours=72, workers=8)
    assert sorted(calls) == sorted(feeds)
    assert len(arts) == 1 and arts[0].title == "Gold rises as dollar slips"   # duplicate across feeds collapsed, stale dropped
