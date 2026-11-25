"""The flip account: lot size from the balance, results from real prices, one chat of its own."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.flip import FlipAccount
from news.store import Store


def call(i=1, side="SELL", entry=3900.0, sl=3930.0, tp1=3891.0, tp2=3866.0, exit_price=None, tp1_hit=False):
    return SimpleNamespace(id=i, side=side, entry=entry, sl=sl, tp1=tp1, tp2=tp2, exit_price=exit_price,
                           tp1_hit_at=datetime.now(timezone.utc) if tp1_hit else None)


def ev(kind, sig, price=None):
    return SimpleNamespace(kind=kind, signal=sig, price=price if price is not None else sig.entry)


def account(tmp_path, **kw):
    return FlipAccount(Store(tmp_path / "f.db"), **kw)


def test_with_200_dollars_the_smallest_lot_already_risks_more_than_the_setting(tmp_path):
    f = account(tmp_path, start=200, risk_pct=10)
    sz = f.size(200.0, 3900.0, 3930.0)                      # $30 stop: 0.01 lot = 1 oz = $30 + spread
    assert sz["lots"] == 0.01 and sz["risk_usd"] == pytest.approx(30.30) and sz["risk_pct"] == pytest.approx(15.2, abs=0.1) and sz["ok"]
    assert f.size(2000.0, 3900.0, 3930.0)["lots"] == 0.06   # 10% of 2000 = $200 / $3000 per lot → 0.066 → rounded DOWN
    assert f.size(25.0, 3900.0, 3930.0)["ok"] is False      # cannot afford even 0.01 lot
    assert f.size(200.0, 3900.0, 3900.0)["ok"] is False     # no stop distance


def test_a_winning_flip_trade_with_one_lot_runs_whole_to_the_exit(tmp_path):
    f = account(tmp_path, start=200, risk_pct=15)
    s = call()
    entry = f.on_event(ev("triggered", s))
    assert "FLIP — 🔴 SELL NOW" in entry and "Lot size <b>0.01</b>" in entry and "cannot be split" in entry and "$200.00" in entry
    assert f.on_event(ev("triggered", s)) is None                         # announced twice: no second position
    assert "single 0.01 lot stays open" in f.on_event(ev("tp1", s))
    s.exit_price, s.tp1_hit_at = 3866.0, datetime.now(timezone.utc)
    close = f.on_event(ev("tp2", s))
    # 1 oz × $34 = +$34.00, minus $0.30 spread: the whole lot ran to TP2 (no half was booked at TP1)
    assert "WIN +$33.70" in close and "$233.70" in close and "1 wins / 0 others in 1 trades" in close
    st = f.state()
    assert st["balance"] == 233.70 and st["open"] is None and st["peak"] == 233.70


def test_a_loss_and_the_next_trade_sizes_from_the_new_balance(tmp_path):
    f = account(tmp_path, start=200, risk_pct=15)
    s = call(1)
    f.on_event(ev("triggered", s))
    s.exit_price = 3930.0
    close = f.on_event(ev("stopped", s))
    assert "LOSS −$30.30" in close and "(-15.2%)" in close and f.state()["balance"] == 169.70
    assert f.on_event(ev("stopped", s)) is None                           # already closed
    assert f.on_event(ev("tp1", call(99))) is None                        # not our position


def test_larger_accounts_book_half_at_tp1(tmp_path):
    f = account(tmp_path, start=4000, risk_pct=15)
    s = call(5, side="BUY", entry=3900.0, sl=3870.0, tp1=3909.0, tp2=3934.0)
    entry = f.on_event(ev("triggered", s))
    assert "Lot size <b>0.20</b>" in entry and "At TP1 close <b>0.10</b> lots" in entry      # $600 / $3000 per lot
    assert "Close <b>0.10</b> lots at 3,909.0" in f.on_event(ev("tp1", s))
    s.exit_price, s.tp1_hit_at = 3870.0, datetime.now(timezone.utc)                           # then stopped on the rest
    f.on_event(ev("stopped", s))
    # 10 oz × +$9 at TP1 = +90; 10 oz × −$30 at the stop = −300; spread 20 oz × 0.30 = −6 → −216
    assert f.state()["trades"][-1]["pnl"] == pytest.approx(-216.0) and f.state()["balance"] == pytest.approx(3784.0)


def test_a_broke_account_skips_the_call_and_reset_starts_over(tmp_path):
    f = account(tmp_path, start=200, risk_pct=15)
    d = f.state()
    d["balance"] = 20.0
    f._save(d)
    msg = f.on_event(ev("triggered", call(7)))
    assert "skipped" in msg and "$20.00" in msg and f.state()["open"] is None
    assert f.reset()["balance"] == 200.0 and f.state()["trades"] == []
    assert "FLIP ACCOUNT" in f.status_text() and "$200.00" in f.status_text()


def test_the_flip_chat_gets_only_flip_messages_and_no_calls_or_alerts(tmp_path, monkeypatch):
    pytest.importorskip("telegram")
    from bot import formatting as fmt
    from bot import telegram_bot as tb
    from config import Settings

    MAIN, FLIP = 111, -100333
    monkeypatch.setattr(fmt, "format_signal_event", lambda e, sizing=None: f"<{e.kind}>")
    st = Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[MAIN], flip_chat_id=FLIP)
    store = Store(tmp_path / "bot.db")
    store.subscribe(MAIN)
    store.subscribe(FLIP)                                              # even if someone typed /start there
    s = call(3)
    pending = [ev("setup", s), ev("triggered", s)]
    tracker = SimpleNamespace(listeners=[], drain_events=lambda: [pending.pop(0) for _ in range(len(pending))], stats=lambda **kw: {})
    bot = tb.SentinelBot(st, store, SimpleNamespace(report=None, pipeline=SimpleNamespace(news_listeners=[]), tracker=tracker))
    bot._sizing = lambda *a, **k: None
    sent = []

    async def fake_send(chat_id, text, **kw):
        sent.append((chat_id, text))

    bot._send = fake_send
    asyncio.run(bot._flush_signal_events())
    assert [t for c, t in sent if c == MAIN] == ["<setup>", "<triggered>"]
    flip = [t for c, t in sent if c == FLIP]
    assert len(flip) == 1 and "FLIP — 🔴 SELL NOW" in flip[0]                       # the setup is not a flip message
    assert bot._main_subs() == [MAIN] and bot._main_subs(alerts_only=True) == [MAIN]
    # without a flip chat nothing of this exists
    plain = tb.SentinelBot(Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[MAIN]),
                           Store(tmp_path / "p.db"), SimpleNamespace(report=None, pipeline=SimpleNamespace(news_listeners=[]), tracker=tracker))
    assert plain.flip is None and plain.flip_chat is None


def test_the_flip_account_can_join_a_call_that_is_already_live_once(tmp_path):
    f = account(tmp_path, start=200, risk_pct=15)
    s = call(35, entry=3551.7, sl=3592.8, tp1=3531.7, tp2=3501.1)
    s.status = "active"
    msg = f.join_open(s, 3557.4)
    assert "FLIP — 🔴 SELL NOW" in msg and "Joining a call that is already live (confirmed at 3,551.7)" in msg
    assert "Entry <b>3,557.4</b>" in msg and "Lot size <b>0.01</b>" in msg
    op = f.state()["open"]
    assert op["entry"] == 3557.4 and op["risk_usd"] == pytest.approx(35.70)          # $35.40 to the stop + spread
    assert f.join_open(s, 3556.0) is None                                             # only once per call
    s.exit_price, s.tp1_hit_at = 3501.1, datetime.now(timezone.utc)
    close = f.on_event(ev("tp2", s))
    assert "WIN +$56.00" in close                                                     # from the JOIN price, not the call's entry


def test_no_late_join_after_target_1_or_outside_the_trade_and_no_retry(tmp_path):
    f = account(tmp_path, start=200, risk_pct=15)
    s = call(36, entry=3551.7, sl=3592.8, tp1=3531.7, tp2=3501.1)
    s.status = "tp1"
    assert f.join_open(s, 3540.0) is None                    # target 1 already hit: too late
    s2 = call(37, entry=3551.7, sl=3592.8, tp1=3531.7, tp2=3501.1)
    s2.status = "active"
    assert f.join_open(s2, 3529.0) is None                   # price already past target 1
    assert f.join_open(s2, 3557.0) is None                   # and it is not tried again for the same call
    assert f.state()["open"] is None
