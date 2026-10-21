"""Feed fetching: conditional requests reuse the parsed articles on 304 Not Modified."""
from __future__ import annotations

from unittest import mock

import news.fetch as f

RSS = (b"<rss><channel><title>T</title><item><title>Gold rises as dollar slips - Reuters</title><link>http://x</link>"
       b"<pubDate>Sun, 19 Oct 2026 05:00:00 GMT</pubDate></item></channel></rss>")


class Resp:
    def __init__(self, code, content=b"", headers=None):
        self.status_code, self.content, self.headers = code, content, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_not_modified_reuses_cached_articles():
    calls = []

    def fake_get(url, headers=None, **kw):
        calls.append(dict(headers))
        if "If-None-Match" in headers:
            return Resp(304)
        return Resp(200, RSS, {"etag": '"abc"', "last-modified": "Sun, 19 Oct 2026 05:00:00 GMT"})

    f._COND.clear()
    with mock.patch.object(f.httpx, "get", fake_get):
        a1, nm1 = f._fetch_feed_ex("http://feed/one")
        a2, nm2 = f._fetch_feed_ex("http://feed/one")
    assert len(a1) == 1 and nm1 is False
    assert len(a2) == 1 and nm2 is True
    assert a2[0] is not a1[0] and a2[0].id == a1[0].id          # fresh copies, same story
    assert calls[1]["If-None-Match"] == '"abc"' and calls[1]["If-Modified-Since"]


def test_feeds_without_validators_are_not_cached():
    def fake_get(url, headers=None, **kw):
        assert "If-None-Match" not in headers
        return Resp(200, RSS)

    f._COND.clear()
    with mock.patch.object(f.httpx, "get", fake_get):
        assert len(f.fetch_feed("http://feed/plain")) == 1
        assert len(f.fetch_feed("http://feed/plain")) == 1
    assert "http://feed/plain" not in f._COND


def test_failed_feed_backs_off_then_recovers():
    calls = []
    state = {"fail": True}

    def fake_get(url, headers=None, **kw):
        calls.append(url)
        if state["fail"]:
            return Resp(503)
        return Resp(200, RSS, {"etag": '"e"'})

    f._COND.clear(); f._BACKOFF.clear()
    with mock.patch.object(f.httpx, "get", fake_get):
        assert f._fetch_feed_ex("http://feed/x") == ([], False)     # 503 → backoff 5 min
        assert f._fetch_feed_ex("http://feed/x") == ([], False)     # cooling down: no request
        assert len(calls) == 1
        retry_at, fails = f._BACKOFF["http://feed/x"]
        assert fails == 1 and 290 < retry_at - f.time.time() <= 300
        f._BACKOFF["http://feed/x"] = (0.0, 1)                       # pretend the cooldown passed
        assert f._fetch_feed_ex("http://feed/x") == ([], False)     # fails again → doubled
        assert f._BACKOFF["http://feed/x"][1] == 2 and f._BACKOFF["http://feed/x"][0] - f.time.time() > 550
        f._BACKOFF["http://feed/x"] = (0.0, 2)
        state["fail"] = False
        arts, _nm = f._fetch_feed_ex("http://feed/x")
        assert len(arts) == 1 and "http://feed/x" not in f._BACKOFF   # success clears the backoff
    assert len(calls) == 3
