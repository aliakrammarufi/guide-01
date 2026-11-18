"""Telegram bot speed paths: concurrent broadcasts, answers from memory, background scans, instant news alerts.
Nothing here talks to Telegram: the send methods are replaced by recorders."""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from telegram.error import Forbidden, RetryAfter, TelegramError  # noqa: E402

from bot import telegram_bot as tb  # noqa: E402
from config import Settings  # noqa: E402
from news.fetch import article_id  # noqa: E402
from news.store import Article, Store  # noqa: E402


def make_bot(tmp_path, cache=None, **over):
    st = Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[], **over)
    store = Store(tmp_path / "bot.db")
    cache = cache or SimpleNamespace(report=None, pipeline=SimpleNamespace(news_listeners=[]), tracker=SimpleNamespace(listeners=[]))
    bot = tb.SentinelBot(st, store, cache)
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id, text, **kw):
        await asyncio.sleep(0.05)                       # one Telegram round trip
        sent.append((chat_id, text))

    bot._send = fake_send
    return bot, store, sent


def report(age_sec: float):
    return SimpleNamespace(generated_at=datetime.now(timezone.utc) - timedelta(seconds=age_sec))


def high_impact(title: str, minutes_ago: float = 5) -> Article:
    return Article(id=article_id(title), title=title, link="https://example.test/a", source="Reuters", summary="",
                   published_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), impact=1.5, confidence=0.8,
                   theme="fed", reason="test", scored_by="lexicon", high_impact=True)


def test_updates_are_processed_concurrently(tmp_path):
    bot, _, _ = make_bot(tmp_path)
    assert bot.app.update_processor.max_concurrent_updates == tb.CONCURRENT_UPDATES == 16


def test_broadcast_serves_chats_side_by_side_and_keeps_the_order_per_chat(tmp_path):
    bot, store, sent = make_bot(tmp_path)
    chats = list(range(1, 13))

    async def run():
        t0 = time.monotonic()
        await bot._broadcast(chats, "hello")
        took = time.monotonic() - t0
        await bot._broadcast_each([(c, [f"first {c}", f"second {c}"]) for c in chats])
        return took

    took = asyncio.run(run())
    assert took < 0.35                                   # 12 chats one after another would take 0.6 s
    assert sorted(sent[:12]) == [(c, "hello") for c in chats]
    for c in chats:
        mine = [text for chat, text in sent[12:] if chat == c]
        assert mine == [f"first {c}", f"second {c}"]


def test_broadcast_unsubscribes_blocked_chats_and_survives_errors(tmp_path):
    bot, store, sent = make_bot(tmp_path)
    for c in (1, 2, 3, 4):
        store.subscribe(c)
    attempts = {4: 0}

    async def fake_send(chat_id, text, **kw):
        if chat_id == 1:
            raise Forbidden("bot was blocked by the user")
        if chat_id == 2:
            raise TelegramError("timed out")
        if chat_id == 4:
            attempts[4] += 1
            if attempts[4] == 1:
                raise RetryAfter(0)                      # flood limit → one more try
        sent.append((chat_id, text))

    bot._send = fake_send
    assert bot._retry_wait(RetryAfter(3)) == 3.5 and bot._retry_wait(RetryAfter(900)) == 30.5   # waits as told, capped
    bot._retry_wait = lambda exc: 0.0
    asyncio.run(bot._broadcast_each([(c, ["a", "b"]) for c in (1, 2, 3, 4)]))
    assert sorted(store.subscribers()) == [2, 3, 4]      # the blocked chat is dropped, a failed send is not
    assert sorted(sent) == [(3, "a"), (3, "b"), (4, "a"), (4, "b")]


def test_broadcast_renders_the_chart_once_and_sends_it_before_the_text(tmp_path):
    bot, _, sent = make_bot(tmp_path)
    renders = []

    async def fake_render(rep):
        renders.append(rep)
        return b"png"

    async def fake_chart(chat_id, rep, png=None):
        assert png == b"png"
        sent.append((chat_id, "<chart>"))

    bot._render, bot._send_chart = fake_render, fake_chart
    rep = object()
    asyncio.run(bot._broadcast_each([(c, [f"report {c}"]) for c in (1, 2, 3)], report=rep, with_chart=True))
    assert renders == [rep]
    for c in (1, 2, 3):
        assert [text for chat, text in sent if chat == c] == ["<chart>", f"report {c}"]


def test_typing_does_not_wait_for_telegram(tmp_path):
    bot, _, _ = make_bot(tmp_path)

    async def run():
        gate, calls = asyncio.Event(), []

        async def send_action(action):
            calls.append(action)
            await gate.wait()

        update = SimpleNamespace(effective_chat=SimpleNamespace(send_action=send_action))
        t0 = time.monotonic()
        await bot._typing(update)                        # returns although Telegram has not answered
        assert time.monotonic() - t0 < 0.5 and len(bot._bg) == 1
        await asyncio.sleep(0)
        assert len(calls) == 1
        gate.set()
        await asyncio.sleep(0.01)
        assert not bot._bg                               # the reference is dropped when the task is done

    asyncio.run(run())


def test_fresh_report_answers_from_memory_and_refreshes_behind_the_reply(tmp_path):
    refreshes, gets = [], []

    async def refresh(**kw):
        refreshes.append(kw)
        await asyncio.sleep(0.6)

    async def get(max_age_sec=None, with_briefing=True):
        gets.append((max_age_sec, with_briefing))
        return "from-pipeline"

    cache = SimpleNamespace(report=report(30), refresh=refresh, get=get, pipeline=SimpleNamespace(news_listeners=[]),
                            tracker=SimpleNamespace(listeners=[]))
    bot, _, _ = make_bot(tmp_path, cache=cache)

    async def run():
        young = cache.report
        assert await bot._fresh_report(120) is young and not refreshes and not gets       # current: nothing to do
        cache.report = older = report(200)
        t0 = time.monotonic()
        assert await bot._fresh_report(120) is older and await bot._fresh_report(120) is older
        assert time.monotonic() - t0 < 0.3                                                 # did not wait for the refresh
        await asyncio.sleep(0)
        assert refreshes == [{"with_briefing": False, "refresh_news": False, "fast": True}]   # one refresh, not one per command
        cache.report = report(700)                                                         # too old to show
        assert await bot._fresh_report(120, get_max_age=120) == "from-pipeline"
        cache.report = None
        assert await bot._fresh_report() == "from-pipeline"
        assert gets == [(120, False), (None, False)]
        await asyncio.gather(*bot._bg)

    asyncio.run(run())


def test_news_command_replies_before_the_scan_finishes(tmp_path):
    release, scans = threading.Event(), []

    def scan_news(feeds=None, min_gap_sec=0.0):
        scans.append(feeds)
        release.wait(5)
        return []

    score = SimpleNamespace(score=12, label="Mixed / quiet news flow", n_bullish=1, n_bearish=0, n_neutral=2, top_themes=[], drivers=[])
    pipe = SimpleNamespace(scan_news=scan_news, news_score=lambda: score, last_full_scan_at=None, news_listeners=[])
    cache = SimpleNamespace(report=None, pipeline=pipe, tracker=SimpleNamespace(listeners=[]))
    bot, _, sent = make_bot(tmp_path, cache=cache)

    async def no_typing(update):
        return None

    bot._typing = no_typing
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=42))

    async def run():
        t0 = time.monotonic()
        await bot.cmd_news(update, None)
        took = time.monotonic() - t0
        assert took < 1.0 and len(sent) == 1 and sent[0][0] == 42 and "NEWS FLOW" in sent[0][1]
        assert scans == [None] and not release.is_set()       # the overdue scan runs behind the reply
        release.set()
        await asyncio.gather(*bot._bg)
        pipe.last_full_scan_at = datetime.now(timezone.utc)   # a recent full scan: no extra scan for the next /news
        await bot.cmd_news(update, None)
        assert scans == [None] and len(sent) == 2

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_fresh_high_impact_news_is_announced_once(tmp_path):
    bot, store, sent = make_bot(tmp_path)
    store.subscribe(1)
    store.subscribe(2)
    store.set_alerts(2, False)

    async def run():
        store.upsert_articles([high_impact("Fed cuts rates by 50 basis points in surprise move")])
        await bot._alert_fresh_news()                            # first start: mark, do not replay old headlines
        assert sent == [] and store.get_state("first_scan_done") is True and store.unalerted_high_impact() == []
        store.upsert_articles([high_impact("Missile strike reported, gold jumps to record high")])
        await asyncio.gather(bot._alert_fresh_news(), bot._alert_fresh_news(), bot._alert_fresh_news())
        assert len(sent) == 1 and sent[0][0] == 1 and "Missile strike reported" in sent[0][1]   # once, alerts-on chats only
        await bot._alert_fresh_news()
        assert len(sent) == 1

    asyncio.run(run())


def test_one_alert_per_story_even_when_several_outlets_carry_it(tmp_path):
    bot, store, sent = make_bot(tmp_path)
    store.subscribe(1)
    store.set_state("first_scan_done", True)
    first = high_impact("Fed’s Waller Says Fed Should Consider Half-Point Rate Hike", minutes_ago=20)
    store.upsert_articles([first])
    asyncio.run(bot._alert_fresh_news())
    assert len(sent) == 1 and "Waller" in sent[0][1]
    # two more outlets with the same story, and one new development
    store.upsert_articles([high_impact("Waller Thinks Fed Should Consider Half-Point Rate Hike", minutes_ago=5),
                           high_impact("Gold plunges to 14-month low as Fed rate hike bets intensify", minutes_ago=3)])
    asyncio.run(bot._alert_fresh_news())
    assert len(sent) == 2 and "Gold plunges" in sent[1][1] and "Waller Thinks" not in sent[1][1]
    assert store.unalerted_high_impact() == []                                   # the repeat is not kept for later either
