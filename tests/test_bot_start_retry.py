"""Telegram start: a failed first contact is retried, the state is visible, listeners are not left behind.
Nothing here talks to Telegram: the Application is replaced by a stand-in."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from telegram.error import InvalidToken, NetworkError  # noqa: E402

from bot import telegram_bot as tb  # noqa: E402
from config import Settings  # noqa: E402
from news.store import Store  # noqa: E402


class FakeApp:
    """Async context manager with the few members run_forever touches."""

    def __init__(self, fail_enter=0, fail_start=0, exc=NetworkError("httpx.ConnectError: ")):
        self.fail_enter, self.fail_start, self.exc = fail_enter, fail_start, exc
        self.enters = self.exits = self.starts = self.stops = 0
        self.running = False
        self.updater = SimpleNamespace(running=False, start_polling=self._start_polling, stop=self._stop_polling)
        self.bot = SimpleNamespace(set_my_commands=self._noop)

    async def _noop(self, *a, **kw):
        return None

    async def __aenter__(self):
        self.enters += 1
        if self.enters <= self.fail_enter:
            raise self.exc
        return self

    async def __aexit__(self, *exc):
        self.exits += 1

    async def start(self):
        self.starts += 1
        if self.starts <= self.fail_start:
            raise self.exc
        self.running = True

    async def stop(self):
        self.stops += 1
        self.running = False

    async def _start_polling(self, **kw):
        self.updater.running = True

    async def _stop_polling(self):
        self.updater.running = False


def make_bot(tmp_path, app, monkeypatch):
    monkeypatch.setattr(tb, "START_RETRY_MIN_SEC", 0.01)
    monkeypatch.setattr(tb, "START_RETRY_MAX_SEC", 0.02)
    st = Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[])
    cache = SimpleNamespace(report=None, pipeline=SimpleNamespace(news_listeners=[]), tracker=SimpleNamespace(listeners=[]))
    bot = tb.SentinelBot(st, Store(tmp_path / "bot.db"), cache)
    bot.app = app
    return bot, cache


async def wait_for(cond, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "condition not reached"
        await asyncio.sleep(0.005)


def test_a_failed_first_contact_is_retried_until_the_bot_polls(tmp_path, monkeypatch):
    app = FakeApp(fail_enter=2)
    bot, cache = make_bot(tmp_path, app, monkeypatch)

    async def run():
        task = asyncio.create_task(bot.run_forever())
        await wait_for(lambda: bot.state == "running")
        st = bot.status()
        assert app.enters == 3 and st["starts_failed"] == 2 and st["last_error"] is None
        assert len(cache.tracker.listeners) == 1 and len(cache.pipeline.news_listeners) == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert bot.state == "stopped" and app.updater.running is False and app.running is False
    assert cache.tracker.listeners == [] and cache.pipeline.news_listeners == []


def test_while_retrying_the_state_and_the_reason_are_reported(tmp_path, monkeypatch):
    app = FakeApp(fail_enter=10**6)
    bot, _ = make_bot(tmp_path, app, monkeypatch)

    async def run():
        task = asyncio.create_task(bot.run_forever())
        await wait_for(lambda: bot.starts_failed >= 3)
        st = bot.status()
        assert st["state"] in ("retrying", "starting") and "NetworkError" in st["last_error"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert bot.state == "stopped"


def test_a_start_that_fails_after_the_listeners_were_added_does_not_leave_them_behind(tmp_path, monkeypatch):
    app = FakeApp(fail_start=2)
    bot, cache = make_bot(tmp_path, app, monkeypatch)

    async def run():
        task = asyncio.create_task(bot.run_forever())
        await wait_for(lambda: bot.state == "running")
        assert app.starts == 3
        assert len(cache.tracker.listeners) == 1 and len(cache.pipeline.news_listeners) == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_an_invalid_token_is_not_retried(tmp_path, monkeypatch):
    app = FakeApp(fail_enter=10**6, exc=InvalidToken("bad token"))
    bot, _ = make_bot(tmp_path, app, monkeypatch)
    with pytest.raises(InvalidToken):
        asyncio.run(bot.run_forever())
    assert app.enters == 1 and bot.state == "stopped" and "InvalidToken" in bot.status()["last_error"]
