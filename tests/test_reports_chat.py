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
    tp1 = fmt.format_trade_result(SimpleNamespace(kind="tp1", signal=trade(None, "tp1"), price=3858.3))
    assert "TARGET 1 HIT" in tp1 and "3,858.3" in tp1 and "TP2 3,832.0" in tp1


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


def _report_job(tmp_path, monkeypatch, **over):
    bot, store, sent = make_bot(tmp_path)
    for k, v in over.items():
        setattr(bot.settings, k, v)
    store.subscribe(REPORTS)
    rep = SimpleNamespace(signal=SimpleNamespace(bias="NEUTRAL", confidence=0, to_dict=lambda: {}), tech=None)

    async def refresh(**kw):
        return rep

    async def nothing(*a, **k):
        return None

    bot.cache.refresh = refresh
    bot.cache.tracker.live = lambda: None
    bot._check_bias_change = nothing
    bot._render = nothing
    bot._full_report = lambda r, chat_id=None: "FULL REPORT"
    asyncio.run(bot.job_report(None))
    return sent


def test_the_scheduled_report_stays_out_of_the_results_board_unless_asked(tmp_path, monkeypatch):
    assert _report_job(tmp_path, monkeypatch) == [(MAIN, "FULL REPORT")]
    assert _report_job(tmp_path / "b", monkeypatch, reports_chat_full_report=True) == [(REPORTS, "FULL REPORT")]


def _closed(i, r, status="tp2"):
    s = trade(r, status)
    s.id, s.traded, s.is_open = i, True, False
    s.closed_at = datetime(2026, 11, 20 + i % 3, 12, 0, tzinfo=timezone.utc)
    return s


def test_the_results_board_introduces_itself_once_with_the_last_closed_calls(tmp_path):
    bot, store, sent = make_bot(tmp_path)
    bot.cache.tracker.history = lambda limit=20: [_closed(9, 0.65), _closed(8, -1.01, "stopped"), _closed(7, 0.85), _closed(6, 0.72)]
    asyncio.run(bot._reports_intro())
    asyncio.run(bot._reports_intro())                       # a restart does not repeat it
    assert len(sent) == 1 and sent[0][0] == REPORTS
    text = sent[0][1]
    assert "RESULTS BOARD" in text and "Live calls are not posted here" in text
    assert text.count("call #") == 3 and "call #9" in text and "call #6" not in text      # the last three, newest first
    assert "WIN" in text and "LOSS" in text and "19 wins / 7 losses of 26" in text
    # the command shows the same list on request, in any chat
    assert "LAST 4 CLOSED CALLS" in bot._recent_results_text()


def test_no_introduction_without_a_reports_chat_and_an_empty_history_says_so(tmp_path):
    bot, store, sent = make_bot(tmp_path, reports=0)
    bot.cache.tracker.history = lambda limit=20: []
    asyncio.run(bot._reports_intro())
    assert sent == [] and "No closed call yet" in bot._recent_results_text()


def test_a_result_says_what_it_is_in_money_and_how_long_the_call_was_open(monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(fmt, "EXAMPLE_ACCOUNT", 10000.0)
    monkeypatch.setattr(fmt, "EXAMPLE_RISK_PCT", 1.0)
    s = trade(0.65)
    s.triggered_at = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)
    s.opened_at = s.triggered_at - timedelta(hours=3)                     # the setup was published earlier: not counted
    s.closed_at = s.triggered_at + timedelta(hours=4, minutes=20)
    text = fmt.format_trade_result(SimpleNamespace(kind="tp2", signal=s, price=3831.6))
    assert "≈ <b>+$65</b> on an example $10,000 account risking 1% per call" in text and "open for 4 h 20 min" in text
    s2 = trade(-1.01, "stopped")
    s2.opened_at, s2.triggered_at = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc), None
    s2.closed_at = s2.opened_at + timedelta(minutes=35)
    loss = fmt.format_trade_result(SimpleNamespace(kind="stopped", signal=s2, price=3902.5))
    assert "−$101" in loss and "open for 35 min" in loss
    monkeypatch.setattr(fmt, "EXAMPLE_ACCOUNT", 0.0)                       # switched off: no money line
    assert "$" not in fmt.format_trade_result(SimpleNamespace(kind="tp2", signal=s, price=3831.6))
    assert fmt._held(SimpleNamespace(triggered_at=None, opened_at=None, closed_at=None)) == ""
    long = SimpleNamespace(triggered_at=s.triggered_at, opened_at=None, closed_at=s.triggered_at + timedelta(hours=53))
    assert fmt._held(long) == "2 d 5 h"


def test_the_day_summary_lists_the_calls_that_closed_that_day(tmp_path, monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(fmt, "EXAMPLE_ACCOUNT", 10000.0)
    monkeypatch.setattr(fmt, "EXAMPLE_RISK_PCT", 1.0)
    bot, store, sent = make_bot(tmp_path)
    day = datetime(2026, 11, 24, 21, 5, tzinfo=timezone.utc)

    def sig(i, r, status, closed):
        s = trade(r, status)
        s.id, s.traded, s.is_open = i, True, False
        s.opened_at = s.triggered_at = closed - timedelta(hours=2)
        s.closed_at = closed
        return s

    rows = [sig(30, 0.65, "tp2", day.replace(hour=9)), sig(31, -1.01, "stopped", day.replace(hour=15)),
            sig(29, 0.80, "tp2", day - timedelta(days=1)),                      # yesterday's: not in today's summary
            SimpleNamespace(id=32, traded=True, is_open=True, result_r=None, closed_at=None)]   # still open
    store.signals_since = lambda since: rows
    text = bot._day_summary_text(day)
    assert "DAY SUMMARY — Tue 24 Nov" in text and "2 calls closed: <b>1 win</b>, <b>1 loss</b>" in text
    assert "day total <b>-0.36R</b>" in text and "≈ −$36" in text
    assert "#30" in text and "#31" in text and "#29" not in text and "#32" not in text
    assert "Past results do not predict future results" in text
    store.signals_since = lambda since: []
    assert "No call closed today." in bot._day_summary_text(day)
    asyncio.run(bot.job_day_summary(None))
    assert sent and sent[-1][0] == REPORTS and "DAY SUMMARY" in sent[-1][1]


def test_the_weekly_scorecard_runs_on_monday_and_the_day_summary_on_weekdays(tmp_path):
    bot, _store, _sent = make_bot(tmp_path)
    jobs = {j.name: j for j in bot.app.job_queue.jobs()}
    # APScheduler's own names: the library maps its 0 = Sunday … 6 = Saturday onto these
    assert str(jobs["scorecard"].job.trigger.fields[4]) == "mon"
    assert str(jobs["day-summary"].job.trigger.fields[4]) == "mon,tue,wed,thu,fri"
    bot2, _s, _t = make_bot(tmp_path / "none", reports=0)
    assert "day-summary" not in {j.name for j in bot2.app.job_queue.jobs()}
