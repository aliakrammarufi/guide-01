"""Pushed news: the stream's payloads become articles, go through the scan path, and are measured.
The stream test talks to a WebSocket server started inside the test on 127.0.0.1 – nothing leaves the machine."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import pipeline as pl
from config import Settings
from news import push
from news.fetch import article_id
from news.store import Article, Store

websockets = pytest.importorskip("websockets")


def make_pipeline(tmp_path) -> pl.Pipeline:
    st = Settings(anthropic_api_key="", calendar_url="", feeds=["https://feeds.example.test/markets.rss"])
    return pl.Pipeline(st, Store(tmp_path / "s.db"))


def payload(title: str, minutes_ago: float = 0.5, **over) -> dict:
    d = {"title": title, "link": "https://www.reuters.com/markets/" + article_id(title), "source": "www.reuters.com",
         "language": "en", "summary": "", "publishDate": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()}
    return d | over


# ---- payload → Article -----------------------------------------------------------
def test_a_pushed_payload_becomes_an_article():
    now = datetime.now(timezone.utc)
    a = push.to_article(payload("Gold jumps as Fed signals a rate cut", publishDate="2026-11-01T12:00:00.000Z"), now)
    assert a.id == article_id("Gold jumps as Fed signals a rate cut") and a.source == "reuters.com"
    assert a.published_at == datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc) and a.fetched_at == now
    assert a.link.startswith("https://www.reuters.com/")


def test_unusable_or_irrelevant_payloads_are_dropped_and_a_future_timestamp_is_clamped():
    now = datetime.now(timezone.utc)
    assert push.to_article(payload("Football club signs a new striker"), now) is None           # nothing to do with gold
    assert push.to_article(payload("Gold rises", link="javascript:alert(1)"), now) is None
    assert push.to_article(payload("", link="https://x.test/a"), now) is None
    assert push.to_article(payload("Junior miner intersects 163 metres of gold", source="www.globenewswire.com"), now) is None
    ahead = push.to_article(payload("Gold rises on dollar weakness", publishDate=(now + timedelta(hours=3)).isoformat()), now)
    assert ahead.published_at == now
    assert push.to_article(payload("Gold rises on dollar weakness", publishDate="not a date"), now).published_at == now


# ---- the pipeline's ingest path ---------------------------------------------------
def test_ingest_stores_scores_and_tells_the_listeners_once(tmp_path):
    pipe = make_pipeline(tmp_path)
    heard = []
    pipe.news_listeners.append(lambda new: heard.append([a.title for a in new]))
    a = push.to_article(payload("Gold surges to record high as Fed cuts rates"))
    assert [x.title for x in pipe.ingest_pushed([a])] == [a.title]
    assert pipe.ingest_pushed([push.to_article(payload("Gold surges to record high as Fed cuts rates"))]) == []   # known now
    stored = pipe.store.recent_articles(24)
    assert len(stored) == 1 and stored[0].scored_by == "lexicon" and stored[0].impact is not None
    assert heard == [[a.title]]


def test_the_lead_over_the_feeds_is_measured_when_a_scan_later_carries_the_headline(tmp_path, monkeypatch):
    pipe = make_pipeline(tmp_path)
    a = push.to_article(payload("Fed's Powell says inflation is still too high"))
    pipe.ingest_pushed([a])
    monkeypatch.setattr(pl.time, "monotonic", lambda t=pl.time.monotonic(): t + 240.0)       # four minutes later …
    same = Article(id=a.id, title=a.title, link="https://feeds.example.test/x", source="Reuters", summary="",
                   published_at=a.published_at)
    monkeypatch.setattr(pl, "fetch_all", lambda feeds, **kw: [same])                         # … a feed carries it
    assert pipe.scan_news() == []                                                            # not new: the stream had it
    assert len(pipe.push_leads) == 1 and 239 <= pipe.push_leads[0] <= 241
    pipe.scan_news()
    assert len(pipe.push_leads) == 1                                                         # counted once


# ---- the stream against a local server -------------------------------------------
class FakeServer:
    def __init__(self, articles, error=None):
        self.articles, self.error = articles, error
        self.headers, self.subscriptions, self.pings = [], [], 0

    async def handler(self, ws):
        self.headers.append(dict(ws.request.headers))
        sub = json.loads(await ws.recv())
        self.subscriptions.append(sub)
        if self.error:
            await ws.send(json.dumps({"action": "error", "data": self.error}))
            await asyncio.sleep(0.2)
            return
        await ws.send(json.dumps({"action": "admit", "leaseId": "L1", "clientNonce": sub["clientNonce"]}))
        for data in self.articles:
            await ws.send(json.dumps({"action": "sendArticle", "data": data}))
        async for raw in ws:
            if json.loads(raw).get("action") == "ping":
                self.pings += 1
                await ws.send(json.dumps({"action": "pong"}))


async def run_stream(tmp_path, server, cap=160, until=None, monkeypatch=None):
    pipe = make_pipeline(tmp_path)
    async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        stream = push.FinlightStream(pipe, "sk_test", query="title:gold", sources=["www.reuters.com"], daily_cap=cap,
                                     url=f"ws://127.0.0.1:{port}/raw")
        task = asyncio.create_task(stream.run())
        end = asyncio.get_running_loop().time() + 5
        while not until(stream):
            assert asyncio.get_running_loop().time() < end, f"not reached: {stream.status()}"
            await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return pipe, stream


def test_the_stream_subscribes_and_stores_what_it_is_sent(tmp_path):
    server = FakeServer([payload("Gold jumps as Fed signals a rate cut"), payload("Football club signs a new striker"),
                         payload("Gold jumps as Fed signals a rate cut")])
    pipe, stream = asyncio.run(run_stream(tmp_path, server, until=lambda s: s.received == 3))
    h = server.headers[0]
    assert h["x-api-key"] == "sk_test" and h["x-takeover"] == "true"
    sub = server.subscriptions[0]
    assert sub["query"] == "title:gold" and sub["sources"] == ["www.reuters.com"] and sub["language"] == "en" and sub["clientNonce"]
    assert "www.globenewswire.com" in sub["excludeSources"]                    # press-release wires are not subscribed to
    st = stream.status()
    assert (st["received"], st["relevant"], st["stored_first"], st["feeds_had_it"], st["today"]) == (3, 2, 1, 1, 3)
    assert st["lag_sec_median"] is not None and 20 <= st["lag_sec_median"] <= 45 and st["state"] == "off"
    assert [a.title for a in pipe.store.recent_articles(24)] == ["Gold jumps as Fed signals a rate cut"]


def test_the_daily_cap_closes_the_stream_for_the_day(tmp_path):
    server = FakeServer([payload(f"Gold headline number {i} lifts bullion") for i in range(6)])
    pipe, stream = asyncio.run(run_stream(tmp_path, server, cap=2, until=lambda s: s.state == "capped"))
    assert stream.received == 2 and pipe.store.get_state("finlight_usage")["n"] == 2
    assert len(server.subscriptions) == 1                    # it did not reconnect to fetch more


def test_a_limit_error_from_the_server_is_not_hammered(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "LIMIT_WAIT_SEC", 30.0)
    server = FakeServer([], error="Monthly dispatch limit reached")
    pipe, stream = asyncio.run(run_stream(tmp_path, server, until=lambda s: s.last_error is not None and s.state == "waiting"))
    assert "limit" in stream.last_error.lower() and len(server.subscriptions) == 1


def test_the_keepalive_ping_is_sent_and_answered(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "PING_EVERY_SEC", 0.05)
    server = FakeServer([])
    asyncio.run(run_stream(tmp_path, server, until=lambda s: server.pings >= 2))
    assert server.pings >= 2
