"""Live news: the scan guard and its listeners, the fast feed list, the event hub, /api/news and the SSE format."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import pipeline as pl
from config import DEFAULT_FEEDS, Settings
from news import live
from news.fetch import article_id
from news.store import Article, Store

GN = "https://news.google.com/rss/search?hl=en-US&q=gold+price"
DIRECT = ["https://feeds.example.test/markets.rss", "https://www.federalreserve.gov/feeds/press_monetary.xml"]


def art(title: str, hours_ago: float = 1.0, link: str | None = None, source: str = "Reuters", impact: float = 0.0,
        high_impact: bool = False) -> Article:
    return Article(id=article_id(title), title=title, link="https://example.test/" + article_id(title) if link is None else link,
                   source=source, summary="", published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                   impact=impact, confidence=0.6, theme="fed", reason="test", scored_by="lexicon", high_impact=high_impact)


def make_pipeline(tmp_path, **over) -> pl.Pipeline:
    # No API key and no calendar URL: nothing here may touch the network.
    st = Settings(anthropic_api_key="", calendar_url="", feeds=[GN] + DIRECT, **over)
    return pl.Pipeline(st, Store(tmp_path / "s.db"))


# ---- scan guard and listeners ----------------------------------------------------
def test_listeners_get_only_the_new_articles(tmp_path, monkeypatch):
    pipe = make_pipeline(tmp_path)
    batches = [[art("Gold rises as the dollar slips"), art("Fed holds rates steady")],
               [art("Gold rises as the dollar slips"), art("Fed holds rates steady"), art("Treasury yields fall after CPI")],
               [art("Gold rises as the dollar slips")]]
    asked = []

    def fake_fetch_all(feeds, **kw):
        asked.append(list(feeds))
        return batches[len(asked) - 1]

    monkeypatch.setattr(pl, "fetch_all", fake_fetch_all)
    heard = []
    pipe.news_listeners.append(lambda new: (_ for _ in ()).throw(RuntimeError("a broken listener")))   # must not break the scan
    pipe.news_listeners.append(lambda new: heard.append([a.title for a in new]))

    assert len(pipe.scan_news()) == 2
    assert [a.title for a in pipe.scan_news()] == ["Treasury yields fall after CPI"]
    assert pipe.last_scan_new == 1
    assert pipe.scan_news() == [] and pipe.last_scan_new == 0
    assert heard == [["Gold rises as the dollar slips", "Fed holds rates steady"], ["Treasury yields fall after CPI"]]   # nothing for the empty scan
    assert asked == [[GN] + DIRECT] * 3                                  # default arguments = every configured feed
    assert pipe.last_scan_at is not None and pipe.last_scan_took is not None
    assert all(a.scored_by for a in pipe.store.recent_articles(24))     # stored and scored as before


def test_min_gap_skips_a_second_full_scan_but_not_a_partial_one(tmp_path, monkeypatch):
    pipe = make_pipeline(tmp_path)
    asked = []
    monkeypatch.setattr(pl, "fetch_all", lambda feeds, **kw: asked.append(list(feeds)) or [])

    pipe.scan_news(DIRECT)                                   # a partial scan is not a full one
    assert pipe.last_scan_at is not None and pipe.last_full_scan_at is None
    pipe.scan_news(min_gap_sec=175)                          # no full scan yet → runs
    assert pipe.last_full_scan_at is not None and len(asked) == 2
    assert pipe.scan_news(min_gap_sec=175) == [] and len(asked) == 2      # too soon → skipped, nothing fetched
    pipe.scan_news(DIRECT, min_gap_sec=175)                  # the gap only applies to full scans
    assert asked[-1] == DIRECT and len(asked) == 3
    pipe.last_full_scan_at -= timedelta(seconds=180)
    pipe.scan_news(min_gap_sec=175)                          # the gap has passed
    assert asked[-1] == [GN] + DIRECT and len(asked) == 4
    pipe.scan_news()                                         # existing callers (no gap) always scan
    assert len(asked) == 5


def test_concurrent_scan_returns_at_once(tmp_path, monkeypatch):
    pipe = make_pipeline(tmp_path)
    started, release, calls = threading.Event(), threading.Event(), []

    def slow_fetch_all(feeds, **kw):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return [art("Gold jumps on safe-haven demand")]

    monkeypatch.setattr(pl, "fetch_all", slow_fetch_all)
    out = []
    t = threading.Thread(target=lambda: out.append(pipe.scan_news()))
    t.start()
    try:
        assert started.wait(5)
        t0 = time.monotonic()
        assert pipe.scan_news() == [] and pipe.scan_news(DIRECT) == []       # not queued behind the running scan
        assert time.monotonic() - t0 < 1.0 and len(calls) == 1
    finally:
        release.set()
        t.join(5)
    assert len(out[0]) == 1
    monkeypatch.setattr(pl, "fetch_all", lambda feeds, **kw: [])
    assert pipe.scan_news() == [] and pipe.last_scan_new == 0               # the guard was released


def test_demo_pipeline_keeps_the_scan_signature(tmp_path):
    from demo import DemoPipeline

    st = Settings(anthropic_api_key="", calendar_url="")
    demo = DemoPipeline(st, Store(tmp_path / "d.db"))
    assert len(demo.scan_news()) > 0
    assert demo.scan_news(None, 175.0) == [] and demo.last_full_scan_at is not None and demo.news_listeners == []


# ---- config ------------------------------------------------------------------------
def test_direct_feeds_exclude_google_news():
    st = Settings(feeds=[GN, "https://NEWS.GOOGLE.COM/rss/search?q=fed"] + DIRECT)
    assert st.direct_feeds == DIRECT
    default = Settings(feeds=list(DEFAULT_FEEDS))
    assert default.direct_feeds and all("news.google.com" not in u for u in default.direct_feeds)
    assert len(default.direct_feeds) + sum("news.google.com" in u for u in DEFAULT_FEEDS) == len(DEFAULT_FEEDS)
    assert live.live_interval(Settings(news_live_interval_sec=5)) == 20          # never below 20 s
    assert live.live_interval(Settings(news_live_interval_sec=90)) == 90
    assert live.live_interval(Settings(news_live_interval_sec=0)) == 0           # switched off


# ---- event hub ---------------------------------------------------------------------
def test_event_hub_fanout_and_drop_oldest():
    async def run():
        hub = live.EventHub()
        a, b = hub.subscribe(), hub.subscribe()
        hub.publish("news", {"n": 1})
        assert a.get_nowait() == ("news", {"n": 1}) and b.get_nowait() == ("news", {"n": 1})
        hub.unsubscribe(b)
        for i in range(105):                                # a is never read: its queue holds 100
            hub.publish("signal", {"i": i})
        assert b.empty() and a.qsize() == 100
        got = [a.get_nowait()[1]["i"] for _ in range(100)]
        assert got[0] == 5 and got[-1] == 104               # the five oldest were dropped, the newest kept
    asyncio.run(run())


def test_event_hub_publish_from_another_thread():
    async def run():
        hub = live.EventHub()
        q = hub.subscribe()
        loop = asyncio.get_running_loop()
        t = threading.Thread(target=hub.publish_threadsafe, args=(loop, "news", {"count": 2}))
        t.start()
        kind, payload = await asyncio.wait_for(q.get(), timeout=2)
        t.join(2)
        assert (kind, payload) == ("news", {"count": 2})
        return hub, loop
    hub, loop = asyncio.run(run())
    hub.publish_threadsafe(loop, "news", {})                # a closed loop (shutdown) is not an error


def test_sse_event_and_article_brief():
    a = art('Powell: "no rush"\nto cut', high_impact=True, impact=-1.2)
    brief = live.article_brief(a)
    assert set(brief) == {"id", "title", "link", "source", "published_at", "fetched_at", "impact", "confidence", "theme",
                          "reason", "scored_by", "high_impact", "direction"}
    assert brief["direction"] == "bearish" and brief["high_impact"] is True and brief["published_at"].endswith("+00:00")
    msg = live.sse_event("news", {"articles": [brief], "count": 1})
    lines = msg.split("\n")
    assert lines[0] == "event: news" and lines[1].startswith("data: ") and lines[2:] == ["", ""]   # one data line, blank line ends it
    assert json.loads(lines[1][len("data: "):]) == {"articles": [brief], "count": 1}


# ---- the fast scan cycle --------------------------------------------------------
def test_news_watch_polls_direct_feeds_and_runs_the_full_scan_when_due(monkeypatch):
    class Stop(Exception):
        pass

    calls, sleeps = [], []

    class Pipe:
        settings = SimpleNamespace(direct_feeds=DIRECT, feeds=[GN] + DIRECT, news_scan_interval_min=3, news_live_interval_sec=5)
        last_full_scan_at = None

        def scan_news(self, feeds=None, min_gap_sec=0.0):
            calls.append((feeds, min_gap_sec))
            if len(calls) == 3:
                raise RuntimeError("feed outage")           # must not end the loop
            if feeds is None:
                self.last_full_scan_at = datetime.now(timezone.utc)
            return []

    pipe = Pipe()

    async def fake_sleep(sec):
        sleeps.append(sec)
        if len(sleeps) == 5:
            pipe.last_full_scan_at -= timedelta(minutes=4)   # the full scan is overdue again
        if len(sleeps) > 5:
            raise Stop

    monkeypatch.setattr(live.asyncio, "sleep", fake_sleep)
    with pytest.raises(Stop):
        asyncio.run(live.news_watch(SimpleNamespace(pipeline=pipe)))
    assert sleeps == [20.0] * 6                                # NEWS_LIVE_INTERVAL_SEC=5 is raised to the 20 s floor
    assert calls == [(None, 175.0), (DIRECT, 0.0), (DIRECT, 0.0), (DIRECT, 0.0), (None, 175.0)]


def test_news_watch_can_be_switched_off():
    pipe = SimpleNamespace(settings=SimpleNamespace(news_live_interval_sec=0), scan_news=lambda *a, **k: pytest.fail("no scan"))
    asyncio.run(asyncio.wait_for(live.news_watch(SimpleNamespace(pipeline=pipe)), timeout=2))


# ---- web: /api/news, /api/health, the listeners that feed the stream ----------------
@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from web.server import create_app

    pipe = make_pipeline(tmp_path)
    engine = SimpleNamespace(subscribe=lambda: None, unsubscribe=lambda q: None, latest=lambda: None,
                             active_source=lambda: "stub", updates=0, stats={})        # never started: no sockets
    cache = SimpleNamespace(report=None, pipeline=pipe, tracker=SimpleNamespace(listeners=[]), history=[],
                            is_stale=lambda: True, last_error=None, backup=None)
    app = create_app(cache, demo=False, engine=engine)
    return SimpleNamespace(app=app, pipe=pipe, cache=cache, client=TestClient(app), TestClient=TestClient)


def test_api_news_reads_the_store_newest_first(web):
    web.pipe.store.upsert_articles([
        art("Gold steadies ahead of payrolls", 2.0, source="Reuters", impact=0.2),
        art("Dollar slides as rate cut bets grow", 1.0, source="CNBC", impact=1.1),
        art("Fed minutes show a split committee", 0.5, link="", impact=0.5),                  # no link → not shown
        art("Yields jump after strong jobs data", 0.3, source="  ", impact=-1.0),              # no source → not shown
        art("Central bank gold buying continues", 0.2, link="ftp://example.test/x"),          # not a web link → not shown
        art("Gold hits record high on safe-haven demand", 0.1, source="Kitco", impact=1.6, high_impact=True),
    ])
    r = web.client.get("/api/news")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert [a["title"] for a in d["articles"]] == ["Gold hits record high on safe-haven demand", "Dollar slides as rate cut bets grow",
                                                   "Gold steadies ahead of payrolls"]
    top = d["articles"][0]
    assert top["direction"] == "bullish" and top["high_impact"] is True and top["age_hours"] == 0.1 and top["source"] == "Kitco"
    assert top["link"].startswith("https://") and top["published_at"].endswith("+00:00")
    assert d["sources"] == 3 and d["demo"] is False and d["last_scan_at"] is None and d["last_scan_new"] == 0
    assert d["scan_interval_sec"] == 60 and d["n_articles"] == 6 and isinstance(d["score"], int) and d["label"]
    assert d["n_bullish"] + d["n_bearish"] + d["n_neutral"] == 6 and all({"theme", "value"} == set(t) for t in d["themes"])
    assert datetime.fromisoformat(d["now"]).tzinfo is not None

    assert [a["source"] for a in web.client.get("/api/news?limit=2").json()["articles"]] == ["Kitco", "CNBC"]
    assert len(web.client.get("/api/news?limit=0").json()["articles"]) == 1                 # clamped to 1..200
    assert len(web.client.get("/api/news?limit=5000").json()["articles"]) == 3
    assert [a["source"] for a in web.client.get("/api/news?hours=1.5").json()["articles"]] == ["Kitco", "CNBC"]
    assert web.client.get("/api/news?limit=abc").status_code == 422


def test_api_news_and_health_report_the_last_scan(web, monkeypatch):
    monkeypatch.setattr(pl, "fetch_all", lambda feeds, **kw: [art("Gold rallies as Fed signals a rate cut", 0.1)])
    web.pipe.scan_news(DIRECT)
    d = web.client.get("/api/news").json()
    assert d["last_scan_new"] == 1 and datetime.fromisoformat(d["last_scan_at"]) == web.pipe.last_scan_at
    h = web.client.get("/api/health").json()
    assert h["ok"] is True and h["news"] == {"last_scan_at": web.pipe.last_scan_at.isoformat(), "last_scan_new": 1, "live_interval_sec": 60}


def test_new_articles_and_signal_events_are_published_to_the_hub(web, monkeypatch):
    monkeypatch.setattr(pl, "fetch_all", lambda feeds, **kw: [art("Gold rallies as Fed signals a rate cut", 0.1, high_impact=True)])
    hub = web.app.state.hub
    assert web.pipe.news_listeners == [] and web.cache.tracker.listeners == []        # nothing before start-up
    for _ in range(2):                                                              # a restart must not register twice
        with web.TestClient(web.app):
            assert len(web.pipe.news_listeners) == 1 and len(web.cache.tracker.listeners) == 1
            q = hub.subscribe()
            new = web.pipe.scan_news()                                               # this thread plays the scan worker
            web.cache.tracker.listeners[0](SimpleNamespace(to_dict=lambda: {"kind": "tp1", "signal_id": 7}))
            deadline = time.monotonic() + 3
            while q.qsize() < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            events = [q.get_nowait() for _ in range(q.qsize())]
            hub.unsubscribe(q)
            assert [k for k, _ in events] == (["news", "signal"] if new else ["signal"])
            if new:
                payload = events[0][1]
                assert payload["count"] == 1 and payload["articles"][0]["title"] == "Gold rallies as Fed signals a rate cut"
                assert payload["articles"][0]["high_impact"] is True and datetime.fromisoformat(payload["at"])
            assert events[-1][1] == {"kind": "tp1", "signal_id": 7}
        assert web.pipe.news_listeners == [] and web.cache.tracker.listeners == []    # removed on shutdown
