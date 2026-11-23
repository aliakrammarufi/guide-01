"""The optional reports chat: scheduled reports, results and the scorecard go there; calls and alerts stay in the main chats."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from bot import formatting as fmt  # noqa: E402
from bot import telegram_bot as tb  # noqa: E402
from config import Settings  # noqa: E402
from news.store import Store  # noqa: E402

MAIN, REPORTS = 111, -100222


def trade(result_r, status="tp2", side="SELL"):
    return SimpleNamespace(id=7, side=side, entry=3867.7, sl=3902.5, tp1=3858.3, tp2=3832.0, exit_price=3831.6, result_r=result_r,
                           status=status, confidence=54, is_live=False)


def make_bot(tmp_path, reports=REPORTS, events=()):
    st = Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[MAIN], reports_chat_id=reports)
    store = Store(tmp_path / "bot.db")
    store.subscribe(MAIN)
    pending = list(events)
    tracker = SimpleNamespace(listeners=[], drain_events=lambda: [pending.pop(0) for _ in range(len(pending))],
                              stats=lambda **kw: {"count": 26, "wins": 19, "losses": 7, "total_r": 8.91})
    cache = SimpleNamespace(report=None, pipeline=SimpleNamespace(news_listeners=[]), tracker=tracker)
    bot = tb.SentinelBot(st, store, cache)
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id, text, **kw):
        sent.append((chat_id, text))

    bot._send = fake_send
    return bot, store, sent


def test_a_closed_call_is_written_up_for_the_reports_chat():
    ev = SimpleNamespace(kind="tp2", signal=trade(0.65), price=3831.6)
    text = fmt.format_trade_result(ev, {"count": 26, "wins": 19, "losses": 7, "total_r": 8.91})
    assert "WIN" in text and "+0.65R" in text and "SELL call #7" in text and "3,867.7" in text and "take profit 2 hit" in text
    assert "19 wins / 7 losses of 26" in text and "+8.91R" in text
    assert "LOSS" in fmt.format_trade_result(SimpleNamespace(kind="stopped", signal=trade(-1.01, "stopped"), price=3902.5))


def test_calls_stay_in_the_main_chat_and_results_also_go_to_the_reports_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(fmt, "format_signal_event", lambda e, sizing=None: f"<{e.kind}>")
    events = [SimpleNamespace(kind="triggered", signal=trade(None, "active"), price=3867.7),
              SimpleNamespace(kind="tp2", signal=trade(0.65), price=3831.6)]
    bot, store, sent = make_bot(tmp_path, events=events)
    bot._sizing = lambda *a, **k: None
    store.subscribe(REPORTS)                       # even if someone typed /start in the reports group
    asyncio.run(bot._flush_signal_events())
    assert [t for c, t in sent if c == MAIN] == ["<triggered>", "<tp2>"]            # the main chat sees every event
    rep = [t for c, t in sent if c == REPORTS]
    assert len(rep) == 1 and "WIN" in rep[0] and "+0.65R" in rep[0]                 # the reports chat only the result


def test_alerts_never_go_to_the_reports_chat_and_it_may_use_commands(tmp_path):
    bot, store, sent = make_bot(tmp_path)
    store.subscribe(REPORTS)
    assert bot._main_subs() == [MAIN] and bot._main_subs(alerts_only=True) == [MAIN]

    async def cmd(update, context):
        await bot._send(update.effective_chat.id, "pong")

    for chat, expect in ((REPORTS, "pong"), (999, None)):
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat, type="group"), effective_message=SimpleNamespace(text="/price"))
        refused = []

        async def send_message(cid, text, **kw):
            refused.append(cid)

        asyncio.run(bot._guard(cmd)(update, SimpleNamespace(bot=SimpleNamespace(send_message=send_message))))
        assert ((chat, "pong") in sent) is (expect is not None) and (refused == [999]) is (chat == 999)


def test_without_a_reports_chat_everything_goes_to_the_subscribers_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(fmt, "format_signal_event", lambda e, sizing=None: f"<{e.kind}>")
    bot, store, sent = make_bot(tmp_path, reports=0, events=[SimpleNamespace(kind="tp2", signal=trade(0.65), price=3831.6)])
    bot._sizing = lambda *a, **k: None
    assert bot.reports_chat is None and bot._main_subs() == [MAIN]
    asyncio.run(bot._flush_signal_events())
    assert sent == [(MAIN, "<tp2>")]
