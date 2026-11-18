"""
Scheduled releases: read the actual number against the forecast the moment it is published.

CPI, payrolls, the Fed decision and a handful of other prints move gold more than any headline, and they come
at a known minute. The bot already warns before them (bot.job_monitor). This module watches the release
itself: from the scheduled second it re-reads the economic calendar every few seconds until the actual value
appears, compares it with the forecast and says which way that usually pushes gold.

It informs, it does not trade: the result goes to the listeners (Telegram alert) and is NOT fed into the
signal rules – no historical test of such a rule exists (see signals/backtest.py).

Source: TradingView's public economic calendar (news/calendar.fetch_tradingview_events), used here for both
the schedule and the actual value so no titles have to be matched across providers.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from news.calendar import Event, fetch_tradingview_events

log = logging.getLogger(__name__)

STATE_KEY = "release_results"
SCHEDULE_EVERY_SEC = 900.0       # re-read the schedule this often
LEAD_SEC = 2.0                   # start asking this long before the scheduled second
FAST_FOR_SEC = 60.0              # every FAST_STEP_SEC for the first minute …
FAST_STEP_SEC = 4.0
SLOW_STEP_SEC = 10.0             # … then every SLOW_STEP_SEC
GIVE_UP_AFTER_SEC = 420.0        # no value after seven minutes: stop asking
LATE_SEC = 600.0                 # a release this far in the past is not announced any more (restart during it)

# How a number ABOVE forecast usually moves gold in the first reaction. "down": strong data / hot inflation
# lifts yields and the dollar. "up": a weaker labour market pulls rate expectations down.
# Order matters: the first match wins ("unemployment rate" before "employment").
RULES: list[tuple[str, str, str]] = [
    ("unemployment rate", "up", "a weaker labour market pulls rate expectations down"),
    ("jobless claims", "up", "more claims mean a weaker labour market"),
    ("unemployment claims", "up", "more claims mean a weaker labour market"),
    ("continuing claims", "up", "more claims mean a weaker labour market"),
    ("non farm", "down", "strong jobs data lifts yields and the dollar"),
    ("nonfarm", "down", "strong jobs data lifts yields and the dollar"),
    ("non-farm", "down", "strong jobs data lifts yields and the dollar"),
    ("payroll", "down", "strong jobs data lifts yields and the dollar"),
    ("adp employment", "down", "strong jobs data lifts yields and the dollar"),
    ("jolts", "down", "more job openings point to a tight labour market"),
    ("average hourly earnings", "down", "faster wage growth keeps rates higher for longer"),
    ("inflation rate", "down", "hotter inflation lifts rate expectations and the dollar"),
    ("cpi", "down", "hotter inflation lifts rate expectations and the dollar"),
    ("consumer price", "down", "hotter inflation lifts rate expectations and the dollar"),
    ("pce", "down", "hotter inflation lifts rate expectations and the dollar"),
    ("ppi", "down", "hotter producer prices lift rate expectations"),
    ("producer price", "down", "hotter producer prices lift rate expectations"),
    ("interest rate decision", "down", "a higher policy rate than expected lifts the dollar"),
    ("fed interest rate", "down", "a higher policy rate than expected lifts the dollar"),
    ("federal funds", "down", "a higher policy rate than expected lifts the dollar"),
    ("gdp", "down", "stronger growth lifts yields and the dollar"),
    ("retail sales", "down", "stronger spending lifts yields and the dollar"),
    ("ism", "down", "stronger activity lifts yields and the dollar"),
    ("pmi", "down", "stronger activity lifts yields and the dollar"),
    ("durable goods", "down", "stronger orders lift yields and the dollar"),
    ("consumer confidence", "down", "stronger confidence lifts yields and the dollar"),
    ("michigan consumer sentiment", "down", "stronger sentiment lifts yields and the dollar"),
    ("philadelphia fed", "down", "stronger activity lifts yields and the dollar"),
    ("philly fed", "down", "stronger activity lifts yields and the dollar"),
    ("industrial production", "down", "stronger output lifts yields and the dollar"),
]
_NUM_RE = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*([kmbt%]?)", re.I)
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_number(text) -> float | None:
    """'0.3%' → 0.3, '198K' → 198000, '-1.2' → -1.2, '' → None."""
    m = _NUM_RE.match(str(text if text is not None else "").replace(",", "."))
    if not m:
        return None
    return float(m.group(1)) * _SCALE.get(m.group(2).lower(), 1.0)


def rule_for(title: str) -> tuple[str, str] | None:
    t = title.lower()
    for key, effect, why in RULES:
        if key in t:
            return effect, why
    return None


def watched(e: Event) -> bool:
    """Worth watching: a rated release with a forecast and a known usual effect on gold."""
    return e.impact in ("High", "Medium") and parse_number(e.forecast) is not None and rule_for(e.title) is not None


def read_release(e: Event, seen_at: datetime | None = None) -> dict | None:
    """The actual value against the forecast, and the usual first reaction of gold. None while there is no value."""
    actual, forecast = parse_number(e.actual), parse_number(e.forecast)
    rule = rule_for(e.title)
    if actual is None or forecast is None or rule is None:
        return None
    effect, why = rule
    tol = max(abs(forecast) * 0.002, 1e-9)                 # equal to the forecast within rounding
    if abs(actual - forecast) <= tol:
        surprise, gold, why_text = "in line", "neutral", "the number matches the forecast, so the reaction is usually small"
    else:
        above = actual > forecast
        surprise = "above forecast" if above else "below forecast"
        gold = ("bearish" if effect == "down" else "bullish") if above else ("bullish" if effect == "down" else "bearish")
        if above:
            why_text = why
        elif effect == "down":
            why_text = "softer than expected, so rate expectations and the dollar tend to ease"
        else:
            why_text = "a stronger labour market than expected keeps rates higher for longer"
    seen = seen_at or datetime.now(timezone.utc)
    return {"key": e.key, "title": e.title, "impact": e.impact, "when": e.when.isoformat(), "actual": e.actual,
            "forecast": e.forecast, "previous": e.previous, "surprise": surprise, "gold": gold, "why": why_text,
            "seen_at": seen.isoformat(), "delay_sec": round((seen - e.when).total_seconds(), 1)}


class ReleaseWatcher:
    def __init__(self, pipeline, fetch=None):
        self.pipeline = pipeline
        self.currencies = list(getattr(pipeline.settings, "calendar_currencies", None) or ["USD"])
        self._fetch = fetch or (lambda: fetch_tradingview_events(self.currencies, days=2, timeout=8.0))
        self.schedule: list[Event] = []
        self.last_schedule_at: datetime | None = None
        self.last_error: str | None = None
        self.listeners: list = []                 # fn(result dict) – called on the event loop

    def _done(self) -> set[str]:
        return {r.get("key") for r in (self.pipeline.store.get_state(STATE_KEY) or [])}

    def results(self) -> list[dict]:
        return list(self.pipeline.store.get_state(STATE_KEY) or [])

    def status(self) -> dict:
        nxt = next((e for e in self.schedule if e.minutes_away > 0), None)
        last = self.results()[-1:] or [None]
        return {"watching": len(self.schedule), "next": nxt.to_dict() if nxt else None, "last_result": last[0],
                "schedule_read_at": self.last_schedule_at.isoformat() if self.last_schedule_at else None, "last_error": self.last_error}

    async def _read(self) -> list[Event]:
        events = await asyncio.to_thread(self._fetch)
        self.last_error = None
        return events

    async def run(self) -> None:
        log.info("Release watch: actual-vs-forecast read for scheduled %s releases", "/".join(self.currencies))
        while True:
            try:
                events = await self._read()
                self.schedule = [e for e in events if watched(e) and e.minutes_away > -LATE_SEC / 60.0]
                self.last_schedule_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("Release watch: schedule read failed: %s", self.last_error)
            # sleep until the next release is due, or until the schedule is read again – whichever comes first
            done = self._done()
            now = datetime.now(timezone.utc)
            pending = [e for e in self.schedule if e.key not in done and (now - e.when).total_seconds() < LATE_SEC]
            due = min((e.when for e in pending), default=None)
            wait = SCHEDULE_EVERY_SEC if due is None else max(0.0, min(SCHEDULE_EVERY_SEC, (due - now).total_seconds() - LEAD_SEC))
            await asyncio.sleep(wait)
            if due is not None and (due - datetime.now(timezone.utc)).total_seconds() <= LEAD_SEC + 0.5:
                await self._watch(due, [e for e in pending if abs((e.when - due).total_seconds()) < 1.0])

    async def _watch(self, due: datetime, batch: list[Event]) -> None:
        """Ask for the actual values of the releases scheduled at ``due`` until they are all in (or time is up)."""
        waiting = {e.key: e for e in batch}
        log.info("Release watch: %s due at %s", ", ".join(e.title for e in batch), due.strftime("%H:%M:%S UTC"))
        while waiting:
            age = (datetime.now(timezone.utc) - due).total_seconds()
            if age > GIVE_UP_AFTER_SEC:
                log.warning("Release watch: no actual value after %.0f s for %s", age, ", ".join(e.title for e in waiting.values()))
                self._store([{"key": k, "title": e.title, "when": e.when.isoformat(), "gave_up": True} for k, e in waiting.items()])
                return
            try:
                fresh = {e.key: e for e in await self._read()}
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                fresh = {}
            for key in list(waiting):
                res = read_release(fresh[key]) if key in fresh else None
                if res is None:
                    continue
                del waiting[key]
                self._store([res])
                log.info("Release: %s actual %s vs forecast %s (%s) → gold %s, seen %.0f s after the scheduled time",
                         res["title"], res["actual"], res["forecast"], res["surprise"], res["gold"], res["delay_sec"])
                for fn in list(self.listeners):
                    try:
                        fn(res)
                    except Exception:  # noqa: BLE001
                        log.exception("release listener failed")
            if waiting:
                await asyncio.sleep(FAST_STEP_SEC if age < FAST_FOR_SEC else SLOW_STEP_SEC)

    def _store(self, rows: list[dict]) -> None:
        kept = (self.pipeline.store.get_state(STATE_KEY) or []) + rows
        self.pipeline.store.set_state(STATE_KEY, kept[-60:])
