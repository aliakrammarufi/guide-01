"""Scheduled releases: the actual number against the forecast, the usual effect on gold, and the watch loop.
The calendar source is replaced by a function that returns prepared events – nothing touches the network."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from bot import formatting as fmt
from config import Settings
from news import releases as rl
from news.calendar import Event
from news.store import Store
import pipeline as pl


def ev(title, forecast="", actual="", impact="High", in_sec=0.0, previous=""):
    return Event(title, "USD", datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=in_sec), impact, forecast, previous, actual)


def test_numbers_with_units_are_read():
    assert rl.parse_number("0.3%") == 0.3 and rl.parse_number("198K") == 198000 and rl.parse_number("-1.2") == -1.2
    assert rl.parse_number("1,5M") == 1500000 and rl.parse_number("4.25") == 4.25
    assert rl.parse_number("") is None and rl.parse_number(None) is None and rl.parse_number("n/a") is None


def test_only_rated_releases_with_a_forecast_and_a_known_effect_are_watched():
    assert rl.watched(ev("Core CPI m/m", "0.3%")) and rl.watched(ev("Initial Jobless Claims", "210K", impact="Medium"))
    assert not rl.watched(ev("FOMC Meeting Minutes"))                       # no number to compare
    assert not rl.watched(ev("Crude Oil Inventories", "1.2M"))              # no known usual effect on gold
    assert not rl.watched(ev("Core CPI m/m", "0.3%", impact="Low"))


def test_the_reading_follows_the_usual_first_reaction_of_gold():
    def gold(title, actual, forecast):
        r = rl.read_release(ev(title, forecast, actual))
        return (r["surprise"], r["gold"])

    assert gold("Core CPI m/m", "0.4%", "0.3%") == ("above forecast", "bearish")       # hot inflation
    assert gold("Core CPI m/m", "0.2%", "0.3%") == ("below forecast", "bullish")
    assert gold("Non Farm Payrolls", "250K", "150K") == ("above forecast", "bearish")  # strong jobs
    assert gold("Non Farm Payrolls", "90K", "150K") == ("below forecast", "bullish")
    assert gold("Unemployment Rate", "4.4%", "4.2%") == ("above forecast", "bullish")  # weak labour market
    assert gold("Unemployment Rate", "4.0%", "4.2%") == ("below forecast", "bearish")
    assert gold("Initial Jobless Claims", "230K", "210K") == ("above forecast", "bullish")
    assert gold("Fed Interest Rate Decision", "4.25", "4.0") == ("above forecast", "bearish")
    assert gold("Retail Sales MoM", "0.3", "0.3") == ("in line", "neutral")
    assert rl.read_release(ev("Core CPI m/m", "0.3%")) is None                          # not printed yet


def test_the_alert_names_the_numbers_and_the_reading():
    text = fmt.format_alert_release(rl.read_release(ev("Core CPI m/m", "0.3%", "0.4%", previous="0.2%")))
    assert "Core CPI m/m" in text and "Actual <b>0.4%</b>" in text and "forecast <b>0.3%</b>" in text and "previous 0.2%" in text
    assert "bearish for gold" in text and "First reaction only" in text


def make_watcher(tmp_path, fetch):
    st = Settings(anthropic_api_key="", calendar_url="", feeds=[])
    return rl.ReleaseWatcher(pl.Pipeline(st, Store(tmp_path / "s.db")), fetch=fetch)


def test_the_watcher_announces_a_release_once_when_its_number_appears(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "FAST_STEP_SEC", 0.02)
    monkeypatch.setattr(rl, "LEAD_SEC", 0.0)
    cpi, claims = ev("Core CPI m/m", "0.3%", in_sec=1), ev("Initial Jobless Claims", "210K", impact="Medium", in_sec=1)
    calls = []

    def fetch():
        calls.append(1)
        printed = len(calls) >= 4                      # the calendar shows the numbers on the fourth read
        return [Event(cpi.title, "USD", cpi.when, "High", "0.3%", "0.2%", "0.4%" if printed else ""),
                Event(claims.title, "USD", claims.when, "Medium", "210K", "", "198K" if printed else ""),
                ev("FOMC Meeting Minutes", in_sec=3600)]

    w = make_watcher(tmp_path, fetch)
    heard = []
    w.listeners.append(heard.append)

    async def run():
        task = asyncio.create_task(w.run())
        end = asyncio.get_running_loop().time() + 6
        while len(heard) < 2:
            assert asyncio.get_running_loop().time() < end, w.status()
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)                        # nothing may be announced twice
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert {(r["title"], r["gold"]) for r in heard} == {("Core CPI m/m", "bearish"), ("Initial Jobless Claims", "bearish")}
    assert len(heard) == 2 and all(0 <= r["delay_sec"] < 5 for r in heard)
    assert [r["title"] for r in w.results()] == [r["title"] for r in heard]      # kept, so a restart does not repeat them


def test_a_release_whose_number_never_appears_is_given_up_without_an_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "FAST_STEP_SEC", 0.02)
    monkeypatch.setattr(rl, "LEAD_SEC", 0.0)
    monkeypatch.setattr(rl, "GIVE_UP_AFTER_SEC", 0.3)
    gdp = ev("GDP Growth Rate QoQ", "2.1%", in_sec=0.5)
    w = make_watcher(tmp_path, lambda: [Event(gdp.title, "USD", gdp.when, "High", "2.1%", "", "")])
    heard = []
    w.listeners.append(heard.append)

    async def run():
        task = asyncio.create_task(w.run())
        end = asyncio.get_running_loop().time() + 6
        while not w.results():
            assert asyncio.get_running_loop().time() < end
            await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert heard == [] and w.results()[0]["gave_up"] is True


def test_a_failing_calendar_source_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "SCHEDULE_EVERY_SEC", 0.05)

    def boom():
        raise RuntimeError("HTTP 403")

    w = make_watcher(tmp_path, boom)

    async def run():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.2)
        assert not task.done() and "HTTP 403" in w.status()["last_error"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
