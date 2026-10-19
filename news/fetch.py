"""
News fetching: pulls RSS/Atom feeds (Google News searches + gold/FX sites),
normalises entries into ``Article`` objects, filters for relevance to gold
and de-duplicates by headline.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import threading
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from calendar import timegm

import feedparser
import httpx

from news.store import Article

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# A headline must mention at least one of these to be kept from the
# general-purpose feeds (Google News queries are already targeted, but the
# filter is cheap and keeps noise out).
RELEVANCE_TERMS = [
    "gold", "xau", "bullion", "precious metal", "comex", "ounce",
    "fed", "fomc", "powell", "rate cut", "rate hike", "interest rate",
    "dollar", "dxy", "greenback", "treasury", "yield",
    "inflation", "cpi", "pce", "payroll", "nonfarm", "non-farm", "jobs report",
    "geopolit", "safe haven", "safe-haven", "war", "missile", "ceasefire",
    "tariff", "trade war", "sanction", "central bank", "pboc", "recession",
    "silver", "metals", "etf flows", "risk-off", "risk off", "risk-on",
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(raw: str | None, limit: int = 400) -> str:
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def normalize_title(title: str) -> str:
    """Lower-case, strip publisher suffix (" - Reuters") and punctuation."""
    t = title.lower()
    t = re.sub(r"\s[-–|]\s[^-–|]{2,40}$", "", t)  # trailing " - Source"
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return _WS_RE.sub(" ", t).strip()


def article_id(title: str) -> str:
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]


def is_relevant(text: str) -> bool:
    t = text.lower()
    return any(term in t for term in RELEVANCE_TERMS)


def _entry_datetime(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                return datetime.fromtimestamp(timegm(tm), tz=timezone.utc)
            except (OverflowError, ValueError):
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return datetime.now(timezone.utc)


def _entry_source(entry, feed_title: str) -> str:
    # Google News puts the real publisher in <source>
    src = entry.get("source")
    if isinstance(src, dict) and src.get("title"):
        return str(src["title"])[:60]
    title = entry.get("title", "")
    m = re.search(r"\s[-–]\s([^-–]{2,40})$", title)
    if m:
        return m.group(1).strip()
    return feed_title[:60] or "unknown"


def parse_feed(content: bytes | str, feed_url: str) -> list[Article]:
    parsed = feedparser.parse(content)
    feed_title = clean_text(parsed.feed.get("title", ""), 80) if parsed.feed else ""
    out: list[Article] = []
    for entry in parsed.entries:
        title = clean_text(entry.get("title"), 220)
        if not title:
            continue
        summary = clean_text(entry.get("summary") or entry.get("description"), 400)
        if not is_relevant(f"{title} {summary}"):
            continue
        out.append(
            Article(
                id=article_id(title),
                title=re.sub(r"\s[-–]\s[^-–]{2,40}$", "", title).strip() or title,
                link=str(entry.get("link", ""))[:500],
                source=_entry_source(entry, feed_title),
                summary=summary,
                published_at=_entry_datetime(entry),
            )
        )
    return out


# Conditional requests: publishers that send ETag / Last-Modified answer "304 Not Modified"
# (a few hundred bytes) when nothing changed, instead of the whole feed every 3 minutes.
_COND: dict[str, tuple[str | None, str | None, list[Article]]] = {}   # url → (etag, last-modified, parsed)
_COND_LOCK = threading.Lock()
_STATS = {"not_modified": 0}


def _fetch_feed_ex(url: str, timeout: float = 15.0) -> tuple[list[Article], bool]:
    """Fetch one feed. Returns (articles, not_modified)."""
    headers = {"User-Agent": USER_AGENT}
    with _COND_LOCK:
        cached = _COND.get(url)
    if cached:
        if cached[0]:
            headers["If-None-Match"] = cached[0]
        if cached[1]:
            headers["If-Modified-Since"] = cached[1]
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        if resp.status_code == 304 and cached:
            with _COND_LOCK:
                _STATS["not_modified"] += 1
            return [replace(a) for a in cached[2]], True
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("Feed failed %s: %s", url, exc)
        return [], False
    try:
        articles = parse_feed(resp.content, url)
    except Exception as exc:  # noqa: BLE001
        log.warning("Feed parse failed %s: %s", url, exc)
        return [], False
    etag, last_mod = resp.headers.get("etag"), resp.headers.get("last-modified")
    if etag or last_mod:
        with _COND_LOCK:
            _COND[url] = (etag, last_mod, [replace(a) for a in articles])
    return articles, False


def fetch_feed(url: str, timeout: float = 15.0) -> list[Article]:
    return _fetch_feed_ex(url, timeout)[0]


def fetch_all(feeds: list[str], max_age_hours: float = 72.0, workers: int = 16, timeout: float = 8.0) -> list[Article]:
    """Fetch every feed concurrently, merge and de-duplicate by headline.

    With ~45 feeds this takes a few seconds instead of a minute, because the
    slow part is waiting on the network, not parsing.
    """
    from concurrent.futures import ThreadPoolExecutor

    seen: dict[str, Article] = {}
    now = datetime.now(timezone.utc)
    started = now
    with _COND_LOCK:
        nm_before = _STATS["not_modified"]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(feeds) or 1))) as pool:
        results = list(pool.map(lambda u: fetch_feed(u, timeout=timeout), feeds))
    with _COND_LOCK:
        unchanged = _STATS["not_modified"] - nm_before
    ok = sum(bool(arts) for arts in results)
    for arts in results:
        for art in arts:
            age_h = (now - art.published_at).total_seconds() / 3600.0
            if age_h > max_age_hours or age_h < -6:  # ignore stale or badly-dated items
                continue
            if art.id not in seen:
                seen[art.id] = art
    articles = sorted(seen.values(), key=lambda a: a.published_at, reverse=True)
    took = (datetime.now(timezone.utc) - started).total_seconds()
    log.info("Fetched %d relevant unique articles from %d/%d feeds in %.1fs (%d unchanged)", len(articles), ok, len(feeds), took, unchanged)
    return articles
