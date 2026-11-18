"""Telegram delivery: a timed-out send is tried once more, the chart is uploaded once, commands are timed.
Nothing here talks to Telegram: the bot's request methods are replaced by recorders."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from telegram.error import BadRequest, Forbidden, TimedOut  # noqa: E402

from bot import telegram_bot as tb  # noqa: E402
from config import Settings  # noqa: E402
from news.store import Store  # noqa: E402


class FakeBot:
    """Stands in for telegram.Bot: records calls, fails the first ``fail`` of them with ``exc``."""

    def __init__(self, fail=0, exc=None):
        self.fail, self.exc = fail, exc or TimedOut()
        self.messages: list[tuple[int, str]] = []
        self.photos: list[tuple[int, object]] = []
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.calls <= self.fail:
            raise self.exc

    async def send_message(self, chat_id, text, **kw):
        self._maybe_fail()
        self.messages.append((chat_id, text))

    async def send_photo(self, chat_id, photo, **kw):
        self._maybe_fail()
        self.photos.append((chat_id, photo))
        return SimpleNamespace(photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="FILE-1")])


def make_bot(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(tb, "SEND_RETRY_PAUSE_SEC", 0.01)
    st = Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[])
    cache = SimpleNamespace(report=None, pipeline=SimpleNamespace(news_listeners=[]), tracker=SimpleNamespace(listeners=[]))
    bot = tb.SentinelBot(st, Store(tmp_path / "bot.db"), cache)
    bot.app = SimpleNamespace(bot=fake)
    return bot


def test_the_request_timeouts_are_longer_than_the_library_default(tmp_path):
    st = Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[])
    bot = tb.SentinelBot(st, Store(tmp_path / "bot.db"), SimpleNamespace(report=None))
    req = bot.app.bot.request
    assert req.read_timeout == 20 and req._client.timeout.connect == 10 and req._client.timeout.write == 20


def test_a_send_that_times_out_is_tried_once_more(tmp_path, monkeypatch):
    fake = FakeBot(fail=1)
    bot = make_bot(tmp_path, monkeypatch, fake)
    asyncio.run(bot._send(7, "hello"))
    assert fake.messages == [(7, "hello")] and fake.calls == 2


def test_a_second_timeout_is_reported_and_a_rejected_request_is_not_repeated(tmp_path, monkeypatch):
    fake = FakeBot(fail=2)
    bot = make_bot(tmp_path, monkeypatch, fake)
    with pytest.raises(TimedOut):
        asyncio.run(bot._send(7, "hello"))
    assert fake.calls == 2

    for exc in (BadRequest("chat not found"), Forbidden("blocked")):
        fake = FakeBot(fail=5, exc=exc)
        bot = make_bot(tmp_path, monkeypatch, fake)
        with pytest.raises(type(exc)):
            asyncio.run(bot._send(7, "hello"))
        assert fake.calls == 1


def test_the_chart_is_uploaded_once_and_the_other_chats_get_it_by_file_id(tmp_path, monkeypatch):
    fake = FakeBot()
    bot = make_bot(tmp_path, monkeypatch, fake)

    async def fake_render(rep):
        return b"PNG"

    bot._render = fake_render
    rep = SimpleNamespace(signal=SimpleNamespace(label="SELL"))
    asyncio.run(bot._broadcast([1, 2, 3, 4], "report", report=rep, with_chart=True))
    sent = [p for _, p in fake.photos]
    assert sorted(c for c, _ in fake.photos) == [1, 2, 3, 4]
    assert sent.count(b"PNG") == 1 and sent.count("FILE-1") == 3
    assert sorted(c for c, _ in fake.messages) == [1, 2, 3, 4]


def test_a_chart_upload_that_times_out_is_retried_and_the_text_still_goes_out(tmp_path, monkeypatch):
    fake = FakeBot(fail=1)
    bot = make_bot(tmp_path, monkeypatch, fake)

    async def fake_render(rep):
        return b"PNG"

    bot._render = fake_render
    rep = SimpleNamespace(signal=SimpleNamespace(label="SELL"))
    asyncio.run(bot._broadcast([1, 2], "report", report=rep, with_chart=True))
    assert len(fake.photos) == 2 and len(fake.messages) == 2


def test_every_command_logs_how_long_the_answer_took(tmp_path, monkeypatch, caplog):
    fake = FakeBot()
    bot = make_bot(tmp_path, monkeypatch, fake)

    async def cmd(update, context):
        await bot._send(update.effective_chat.id, "pong")

    update = SimpleNamespace(effective_chat=SimpleNamespace(id=7, type="private"), effective_message=SimpleNamespace(text="/price now"))
    with caplog.at_level(logging.INFO, logger="bot.telegram_bot"):
        asyncio.run(bot._guard(cmd)(update, SimpleNamespace(bot=fake)))
    assert fake.messages == [(7, "pong")]
    assert any(r.getMessage().startswith("/price answered in ") for r in caplog.records)


def test_a_broadcast_survives_a_short_telegram_outage(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "BROADCAST_RETRY_SEC", (0.01, 0.01, 0.01, 0.01))
    fake = FakeBot(fail=5, exc=TimedOut())             # the quick retry and two more rounds fail, the fourth round gets through
    bot = make_bot(tmp_path, monkeypatch, fake)
    asyncio.run(bot._broadcast([7], "TP1 hit"))
    assert fake.messages == [(7, "TP1 hit")]


def test_a_broadcast_gives_up_after_the_last_try_and_does_not_repeat_a_rejected_message(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(tb, "BROADCAST_RETRY_SEC", (0.01, 0.01))
    fake = FakeBot(fail=10**6, exc=TimedOut())
    bot = make_bot(tmp_path, monkeypatch, fake)
    with caplog.at_level(logging.ERROR, logger="bot.telegram_bot"):
        asyncio.run(bot._broadcast([7], "report"))
    assert fake.messages == [] and fake.calls == 6     # three rounds, each with its quick retry
    assert any("failed for good" in r.getMessage() for r in caplog.records)

    fake = FakeBot(fail=10**6, exc=BadRequest("message is too long"))
    bot = make_bot(tmp_path, monkeypatch, fake)
    asyncio.run(bot._broadcast([7], "report"))
    assert fake.calls == 1


def test_calls_carry_a_size_guide_by_confidence():
    from bot import formatting as fmt
    assert "half" in fmt.size_guide(52) and "52%" in fmt.size_guide(52)
    assert "normal risk" in fmt.size_guide(68) and "half" not in fmt.size_guide(68)
