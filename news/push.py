"""
Pushed news: a live headline stream (finlight raw WebSocket) next to the polled feeds.

The feeds are asked every minute; a stream is told. Each pushed headline goes through the same path as a
scanned one (relevance filter, de-duplication by headline, scoring, store, listeners), so the terminal and the
Telegram alerts need no change: they hear about it through ``pipeline.news_listeners`` seconds after it arrives.

Protocol (finlight-client 2.4.0, ``_base_websocket_client.py``):
  connect   wss://wss.finlight.me/raw  with headers x-api-key, x-client-version, x-takeover
  send      {"query": …, "sources": […], "language": "en", "includeUpdates": false, "clientNonce": …}
  receive   {"action": "admit" | "pong" | "sendArticle" | "preempted" | "admin_kick" | "error", …}
  keepalive {"action": "ping", "t": <ms>} every 25 s; the server limits a connection to 2 h

The plan counts every delivered headline (Pro Light: 5,000 a month), so the subscription is narrow (see
DEFAULT_QUERY) and a daily cap closes the stream for the rest of the UTC day once it is reached.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from news.fetch import article_id, clean_text, is_relevant
from news.store import Article

log = logging.getLogger(__name__)

WSS_URL = "wss://wss.finlight.me/raw"
CLIENT_VERSION = "python/xau-sentinel"
PING_EVERY_SEC = 25.0
PONG_TIMEOUT_SEC = 60.0
ROTATE_AFTER_SEC = 115 * 60          # the server closes a connection after 2 h: reconnect before it does
RECONNECT_MIN_SEC = 1.0
RECONNECT_MAX_SEC = 60.0
PREEMPTED_WAIT_SEC = 600.0           # another client took the stream over (e.g. the new instance during a deploy)
LIMIT_WAIT_SEC = 900.0               # the server said "limit": do not hammer it
# Headline terms only: every delivered article counts against the plan, so no broad full-text search here.
DEFAULT_QUERY = ("title:gold OR title:bullion OR title:XAU OR title:Fed OR title:FOMC OR title:Powell OR title:inflation "
                 "OR title:CPI OR title:payrolls OR title:Treasury OR title:dollar OR title:tariff OR title:ceasefire")


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _source_name(domain: str) -> str:
    d = (domain or "").strip().lower()
    return d[4:] if d.startswith("www.") else d


def to_article(data: dict, now: datetime | None = None) -> Article | None:
    """A ``sendArticle`` payload as an Article, or None when it is unusable or not about gold's drivers."""
    now = now or datetime.now(timezone.utc)
    title = clean_text(data.get("title"), 220)
    link = str(data.get("link") or "")[:500]
    if not title or not link.startswith("http"):
        return None
    summary = clean_text(data.get("summary"), 400)
    if not is_relevant(f"{title} {summary}"):
        return None
    published = _parse_time(data.get("publishDate")) or now
    if published > now:                       # a publisher clock ahead of ours must not sort to the top for hours
        published = now
    return Article(id=article_id(title), title=title, link=link, source=_source_name(str(data.get("source") or "")) or "finlight",
                   summary=summary, published_at=published, fetched_at=now)


class FinlightStream:
    def __init__(self, pipeline, api_key: str, query: str = "", sources: list[str] | None = None, daily_cap: int = 160,
                 url: str = WSS_URL):
        self.pipeline = pipeline
        self.api_key = api_key
        self.query = query.strip() or DEFAULT_QUERY
        self.sources = [s for s in (sources or []) if s]
        self.daily_cap = max(0, int(daily_cap))
        self.url = url
        self.state = "starting"               # starting / connected / waiting / capped / off
        self.state_since = datetime.now(timezone.utc)
        self.last_error: str | None = None
        self.connects = 0
        self.received = 0                     # delivered by the server since start (each one counts against the plan)
        self.relevant = 0
        self.new = 0                          # stored first by the stream (the feeds had not seen it yet)
        self.known = 0                        # a feed scan had stored it before the stream delivered it
        self.last_article_at: datetime | None = None
        self._lags: deque[float] = deque(maxlen=200)      # seconds from the publisher's timestamp to arrival
        self._last_pong = time.monotonic()

    # ---------------------------------------------------------------- status
    def _set(self, state: str, error: str | None = None) -> None:
        if state != self.state:
            self.state, self.state_since = state, datetime.now(timezone.utc)
        if error is not None:
            self.last_error = error[:300]

    def _usage(self) -> tuple[str, int]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        d = self.pipeline.store.get_state("finlight_usage") or {}
        return today, int(d.get("n", 0)) if d.get("day") == today else 0

    def _count_delivery(self) -> int:
        today, n = self._usage()
        self.pipeline.store.set_state("finlight_usage", {"day": today, "n": n + 1})
        return n + 1

    def status(self) -> dict:
        lags = sorted(self._lags)
        lead = sorted(getattr(self.pipeline, "push_leads", []) or [])
        return {
            "state": self.state, "since": self.state_since.isoformat(), "connects": self.connects,
            "received": self.received, "relevant": self.relevant, "stored_first": self.new, "feeds_had_it": self.known,
            "today": self._usage()[1], "daily_cap": self.daily_cap,
            "last_article_at": self.last_article_at.isoformat() if self.last_article_at else None,
            "lag_sec_median": round(statistics.median(lags), 1) if lags else None,
            "lag_sec_fastest": round(lags[0], 1) if lags else None,
            "lead_over_feeds_sec_median": round(statistics.median(lead), 1) if lead else None,
            "lead_samples": len(lead),
            "last_error": self.last_error,
        }

    # --------------------------------------------------------------- running
    async def run(self) -> None:
        """Connect, listen, reconnect – until cancelled."""
        import websockets

        log.info("Pushed news: finlight raw stream, query %r, sources %s, at most %d headlines a day",
                 self.query, self.sources or "default", self.daily_cap)
        delay = RECONNECT_MIN_SEC
        while True:
            wait = delay
            try:
                if self.daily_cap and self._usage()[1] >= self.daily_cap:
                    wait = self._seconds_to_midnight()
                    self._set("capped")
                    log.warning("finlight: daily cap of %d headlines reached – stream closed for %.0f min", self.daily_cap, wait / 60)
                else:
                    headers = {"x-api-key": self.api_key, "x-client-version": CLIENT_VERSION, "x-takeover": "true"}
                    try:
                        ctx = websockets.connect(self.url, additional_headers=headers, ping_interval=None, max_size=2**22)
                    except TypeError:                   # websockets < 13
                        ctx = websockets.connect(self.url, extra_headers=headers, ping_interval=None, max_size=2**22)
                    async with ctx as ws:
                        self.connects += 1
                        delay = RECONNECT_MIN_SEC
                        wait = await self._session(ws) or delay
            except asyncio.CancelledError:
                self._set("off")
                raise
            except Exception as exc:  # noqa: BLE001
                self._set("waiting", f"{type(exc).__name__}: {exc}")
                log.warning("finlight stream error: %s (reconnect in %.0f s)", self.last_error, wait)
                delay = min(RECONNECT_MAX_SEC, delay * 2)
            if self.state not in ("capped",):
                self._set("waiting")
            await asyncio.sleep(wait)

    @staticmethod
    def _seconds_to_midnight() -> float:
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        return max(60.0, (nxt - now).total_seconds())

    async def _session(self, ws) -> float | None:
        """One connection. Returns how long to wait before the next one (None = the usual back-off)."""
        sub = {"query": self.query, "sources": self.sources or None, "excludeSources": None, "language": "en",
               "includeUpdates": False, "clientNonce": str(uuid.uuid4())}
        await ws.send(json.dumps(sub))
        self._last_pong = time.monotonic()
        started = time.monotonic()
        pinger = asyncio.create_task(self._ping(ws))
        try:
            while True:
                left = ROTATE_AFTER_SEC - (time.monotonic() - started)
                if left <= 0:
                    log.info("finlight: rotating the connection before the 2 h limit")
                    return RECONNECT_MIN_SEC
                if time.monotonic() - self._last_pong > PONG_TIMEOUT_SEC:
                    raise ConnectionError("no pong from the server")
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(left, 10.0))
                except asyncio.TimeoutError:
                    continue
                wait = await self._handle(raw)
                if wait is not None:
                    return wait
        finally:
            pinger.cancel()
            await asyncio.gather(pinger, return_exceptions=True)

    async def _ping(self, ws) -> None:
        while True:
            await asyncio.sleep(PING_EVERY_SEC)
            await ws.send(json.dumps({"action": "ping", "t": int(time.time() * 1000)}))

    async def _handle(self, raw) -> float | None:
        """One server message. Returns a wait (seconds) when the connection has to be closed, else None."""
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return None
        action = msg.get("action")
        if action == "pong":
            self._last_pong = time.monotonic()
        elif action == "admit":
            self._set("connected")
            self.last_error = None
            log.info("finlight: stream connected (lease %s)", msg.get("leaseId"))
        elif action == "sendArticle":
            self._last_pong = time.monotonic()
            await self._on_article(msg.get("data") or {})
            if self.daily_cap and self._usage()[1] >= self.daily_cap:
                return 1.0                                  # run() sees the cap and sleeps until midnight
        elif action == "preempted":
            self._set("waiting", f"preempted: {msg.get('reason', 'another client took the stream over')}")
            log.warning("finlight: %s – next try in %.0f min", self.last_error, PREEMPTED_WAIT_SEC / 60)
            return PREEMPTED_WAIT_SEC
        elif action == "admin_kick":
            wait = max(60.0, float(msg.get("retryAfter", 900000)) / 1000.0)
            self._set("waiting", f"kicked by the server, retry after {wait:.0f} s")
            log.warning("finlight: %s", self.last_error)
            return wait
        elif action == "error":
            err = str(msg.get("data") or msg.get("error") or "unknown error")
            self._set(self.state, err)
            log.error("finlight: server error: %s", err[:300])
            low = err.lower()
            if "limit" in low:
                return LIMIT_WAIT_SEC
            if "blocked" in low:
                return 3600.0
        return None

    async def _on_article(self, data: dict) -> None:
        now = datetime.now(timezone.utc)
        self.received += 1
        self.last_article_at = now
        used = await asyncio.to_thread(self._count_delivery)
        art = to_article(data, now)
        if art is None:
            log.info("finlight: skipped (not relevant) %r [%d today]", str(data.get("title"))[:90], used)
            return
        self.relevant += 1
        lag = (now - art.published_at).total_seconds()
        self._lags.append(lag)
        new = await asyncio.to_thread(self.pipeline.ingest_pushed, [art])
        if new:
            self.new += 1
        else:
            self.known += 1
        log.info("finlight: %s %r (%s) %.0f s after publication%s [%d today]", "NEW" if new else "known", art.title[:90], art.source,
                 lag, " · HIGH IMPACT" if (new and new[0].high_impact) else "", used)
