"""Report spacing, quiet hours, the result chart and the daily loss limit."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("telegram")

from bot import telegram_bot as tb  # noqa: E402
from bot.chart import render_trade_chart  # noqa: E402
from config import Settings  # noqa: E402
from news.store import Store  # noqa: E402
from signals.tracker import SignalTracker, TradeSignal  # noqa: E402


def make_bot(tmp_path, **over):
    st = Settings(telegram_token="123456:TEST", anthropic_api_key="", calendar_url="", allowed_chat_ids=[], **over)
    cache = SimpleNamespace(report=None, pipeline=SimpleNamespace(news_listeners=[]), tracker=SimpleNamespace(listeners=[]))
    return tb.SentinelBot(st, Store(tmp_path / "bot.db"), cache)


def test_the_report_can_be_spaced_without_touching_the_analysis(tmp_path):
    default = make_bot(tmp_path / "a")
    assert default.report_every_min == default.settings.analysis_interval_min == 15
    hourly = make_bot(tmp_path / "b", report_interval_min=60)
    assert hourly.report_every_min == 60 and hourly.settings.analysis_interval_min == 15
    job = {j.name: j for j in hourly.app.job_queue.jobs()}["report"]
    assert job.job.trigger.interval == timedelta(minutes=60)


def test_quiet_hours_are_read_and_wrap_past_midnight(tmp_path):
    P = tb.SentinelBot._parse_quiet
    assert P("05:00-13:00") == (300, 780) and P(" 22:30 - 06:00 ") == (1350, 360)
    assert P("") is None and P("25:00-06:00") is None and P("nonsense") is None and P("06:00-06:00") is None
    day = make_bot(tmp_path / "a", quiet_hours_utc="05:00-13:00")
    at = lambda h, m=0: datetime(2026, 11, 24, h, m, tzinfo=timezone.utc)   # noqa: E731
    assert day._quiet_now(at(5)) and day._quiet_now(at(12, 59)) and not day._quiet_now(at(13)) and not day._quiet_now(at(4, 59))
    night = make_bot(tmp_path / "b", quiet_hours_utc="22:30-06:00")
    assert night._quiet_now(at(23)) and night._quiet_now(at(2)) and not night._quiet_now(at(6)) and not night._quiet_now(at(22, 29))
    assert not make_bot(tmp_path / "c")._quiet_now(at(3))                    # off by default


def test_in_quiet_hours_messages_still_go_out_but_without_sound(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, quiet_hours_utc="00:00-23:59")
    calls = []

    async def send_message(chat_id, text, **kw):
        calls.append(kw)

    bot.app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    asyncio.run(bot._send(7, "TP1 hit"))
    assert calls and calls[0].get("disable_notification") is True
    loud = make_bot(tmp_path / "loud")
    calls.clear()
    loud.app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    asyncio.run(loud._send(7, "TP1 hit"))
    assert "disable_notification" not in calls[0]


def _bars(n=400):
    rng = np.random.default_rng(5)
    idx = pd.date_range("2026-11-16", periods=n, freq="15min", tz="UTC")
    close = 3880 + np.cumsum(rng.normal(-0.25, 2.2, n))
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, close) + 1, "low": np.minimum(o, close) - 1, "close": close}, index=idx)


def test_the_result_chart_is_drawn_when_the_bars_cover_the_trade():
    df = _bars()
    t0, t1 = df.index[150].to_pydatetime(), df.index[230].to_pydatetime()
    e = float(df["close"].iloc[150])
    tr = SimpleNamespace(id=27, side="SELL", entry=e, sl=e + 30, tp1=e - 9, tp2=e - 34, exit_price=e - 34, result_r=0.65,
                         triggered_at=t0, opened_at=t0, closed_at=t1)
    png = render_trade_chart(df, tr)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20000
    assert render_trade_chart(df.iloc[200:], tr) is None           # the candles start after the entry: no picture
    assert render_trade_chart(None, tr) is None
    tr.closed_at = None
    assert render_trade_chart(df, tr) is None


def _closed(store, when, r):
    s = TradeSignal(id=0, side="SELL", entry=3900.0, sl=3930.0, tp1=3890.0, tp2=3860.0, confidence=55, opened_at=when - timedelta(hours=2),
                    status="stopped" if r < 0 else "tp2")
    s.id = store.insert_signal(s)
    s.triggered_at, s.closed_at, s.exit_price, s.result_r = when - timedelta(hours=2), when, 3930.0 if r < 0 else 3860.0, r
    store.update_signal(s)
    return s


def test_the_daily_loss_limit_counts_only_todays_losing_calls(tmp_path):
    store = Store(tmp_path / "t.db")
    now = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
    _closed(store, now - timedelta(days=1), -1.0)                   # yesterday's loss does not count
    _closed(store, now - timedelta(hours=5), 0.7)                   # a win does not count
    _closed(store, now - timedelta(hours=3), -1.0)
    off = SignalTracker(store)
    assert off.losses_today(now) == 1 and not off.loss_limit_reached(now)            # off by default
    two = SignalTracker(store, max_losses_per_day=2)
    assert not two.loss_limit_reached(now)
    _closed(store, now - timedelta(hours=1), -1.01)
    assert two.losses_today(now) == 2 and two.loss_limit_reached(now)
    assert not two.loss_limit_reached(now + timedelta(days=1))                       # a new day starts clean
    from signals.backtest import ReplayParams
    assert "max_losses_per_day" not in ReplayParams().to_dict() and ReplayParams(max_losses_per_day=2).to_dict()["max_losses_per_day"] == 2
