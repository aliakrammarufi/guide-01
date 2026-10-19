"""
Economic calendar: upcoming scheduled releases that move gold (CPI, FOMC,
payrolls, PCE, GDP, Fed speakers…).

Source: ForexFactory's free weekly JSON (configurable).  Cached for 30 min.
Only the configured currencies (default USD) are kept, and only medium/high
impact events.  Everything degrades gracefully: if the calendar is
unreachable the bot simply has no events to show.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

# Events that historically move gold the most get a boost in the "gold weight"
HEAVY = ["fomc", "federal funds", "rate decision", "cpi", "nonfarm", "non-farm", "pce", "powell",
         "gdp", "unemployment", "ism", "retail sales", "jobless", "ppi", "fed chair", "press conference"]


@dataclass
class Event:
    title: str
    currency: str
    when: datetime             # UTC
    impact: str                # "High" | "Medium" | "Low"
    forecast: str = ""
    previous: str = ""
    actual: str = ""

    @property
    def key(self) -> str:
        return f"{self.currency}|{self.title}|{self.when.isoformat()}"

    @property
    def minutes_away(self) -> float:
        return (self.when - datetime.now(timezone.utc)).total_seconds() / 60.0

    @property
    def gold_weight(self) -> int:
        t = self.title.lower()
        base = {"High": 3, "Medium": 2}.get(self.impact, 1)
        return base + (1 if any(h in t for h in HEAVY) else 0)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "currency": self.currency, "when": self.when.isoformat(),
            "impact": self.impact, "forecast": self.forecast, "previous": self.previous,
            "actual": self.actual, "minutes_away": round(self.minutes_away, 1), "gold_weight": self.gold_weight,
        }


def _parse_when(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_events(payload: list[dict], currencies: list[str]) -> list[Event]:
    out: list[Event] = []
    wanted = {c.upper() for c in currencies}
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        cur = str(item.get("country") or item.get("currency") or "").upper()
        if wanted and cur not in wanted:
            continue
        impact = str(item.get("impact") or "Low").title()
        if impact not in ("High", "Medium"):
            continue
        when = _parse_when(str(item.get("date") or ""))
        if when is None:
            continue
        out.append(Event(
            title=str(item.get("title") or "").strip()[:80],
            currency=cur, when=when, impact=impact,
            forecast=str(item.get("forecast") or "").strip(),
            previous=str(item.get("previous") or "").strip(),
            actual=str(item.get("actual") or "").strip(),
        ))
    return sorted(out, key=lambda e: e.when)


# ---------------------------------------------------------------------------
# Fallback source: Nasdaq's public economic-calendar JSON (per day, no key).
# It carries no impact rating, so we classify by event name.
# ---------------------------------------------------------------------------
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/economic?date={date}"
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/",
}
COUNTRY_TO_CCY = {"united states": "USD", "us": "USD", "usa": "USD", "euro area": "EUR", "eurozone": "EUR",
                  "united kingdom": "GBP", "japan": "JPY", "china": "CNY", "canada": "CAD", "australia": "AUD",
                  "switzerland": "CHF", "new zealand": "NZD", "germany": "EUR"}
HIGH_IMPACT = ["fomc", "fed interest rate", "federal funds", "rate decision", "interest rate decision", "cpi",
               "consumer price", "nonfarm", "non-farm", "payroll", "pce", "gdp", "unemployment rate",
               "ism manufacturing", "ism services", "ism non-manufacturing", "retail sales", "jobless claims",
               "ppi", "producer price", "powell", "fed chair", "jolts", "michigan", "consumer confidence",
               "durable goods", "fomc minutes", "adp", "average hourly earnings", "core inflation"]
MEDIUM_IMPACT = ["pmi", "trade balance", "factory orders", "existing home", "new home", "pending home",
                 "housing starts", "building permits", "philadelphia fed", "philly fed", "empire state",
                 "chicago", "richmond", "dallas fed", "kansas", "crude oil", "capacity utilization",
                 "industrial production", "personal income", "personal spending", "wholesale", "business inventories",
                 "import price", "export price", "current account", "fed ", "fed's", "treasury", "auction",
                 "budget", "cb leading", "construction spending", "nahb", "case-shiller", "beige book"]


def classify_impact(title: str) -> str:
    t = title.lower()
    if any(k in t for k in HIGH_IMPACT):
        return "High"
    if any(k in t for k in MEDIUM_IMPACT):
        return "Medium"
    return "Low"


def fetch_nasdaq_events(currencies: list[str], days: int = 7, timeout: float = 15.0) -> list[Event]:
    wanted = {c.upper() for c in currencies}
    out: list[Event] = []
    today = datetime.now(timezone.utc).date()
    logged = False
    for i in range(days):
        day = today + timedelta(days=i)
        r = httpx.get(NASDAQ_URL.format(date=day.isoformat()), headers=NASDAQ_HEADERS, timeout=timeout)
        r.raise_for_status()
        body = r.json() or {}
        rows = ((body.get("data") or {}).get("rows") or []) if isinstance(body, dict) else []
        if rows and not logged:
            log.info("Nasdaq calendar sample row: %s", {k: rows[0].get(k) for k in list(rows[0])[:8]})
            logged = True
        for row in rows:
            if not isinstance(row, dict):
                continue
            cur = COUNTRY_TO_CCY.get(str(row.get("country") or "").strip().lower(), "")
            if wanted and cur not in wanted:
                continue
            name = str(row.get("eventName") or row.get("event") or row.get("name") or "").strip()
            m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(row.get("gmt") or row.get("time") or ""))
            if not name or not m:
                continue
            impact = classify_impact(name)
            if impact == "Low":
                continue
            when = datetime(day.year, day.month, day.day, int(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)
            out.append(Event(title=name[:80], currency=cur, when=when, impact=impact,
                             forecast=str(row.get("consensus") or "").strip(),
                             previous=str(row.get("previous") or "").strip(),
                             actual=str(row.get("actual") or "").strip()))
    return sorted(out, key=lambda e: e.when)


class CalendarService:
    def __init__(self, url: str, currencies: list[str], ttl_sec: int = 1800):
        self.url = url
        self.currencies = currencies
        self.ttl = ttl_sec
        self._events: list[Event] = []
        self._ts = 0.0
        self._lock = threading.Lock()

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
    }

    def refresh(self) -> list[Event]:
        if not self.url:
            return []
        def forexfactory() -> list[Event]:
            r = httpx.get(self.url, timeout=15, headers=self.HEADERS, follow_redirects=True)
            r.raise_for_status()
            return parse_events(r.json(), self.currencies)

        errors: list[str] = []
        for name, source in (("forexfactory", forexfactory), ("nasdaq", lambda: fetch_nasdaq_events(self.currencies))):
            try:
                events = source()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            if not events and name == "forexfactory":
                errors.append("forexfactory: no events")
                continue
            with self._lock:
                self._events, self._ts = events, time.time()
            log.info("Calendar (%s): %d %s events in the next days", name, len(events), "/".join(self.currencies))
            return self.events()
        log.warning("Calendar fetch failed: %s", " | ".join(errors))
        with self._lock:
            # retry in 5 min (rate limits are short-lived), but keep whatever we had
            self._ts = time.time() - self.ttl + 300
        return self.events()

    def events(self) -> list[Event]:
        with self._lock:
            stale = time.time() - self._ts > self.ttl
            current = list(self._events)
        if not stale:
            return current
        if not current:
            return self.refresh()          # first load: wait for it
        threading.Thread(target=self.refresh, daemon=True).start()  # later: refresh in the background
        return current

    def upcoming(self, hours: float = 48.0) -> list[Event]:
        now = datetime.now(timezone.utc)
        return [e for e in self.events() if now - timedelta(minutes=15) <= e.when <= now + timedelta(hours=hours)]

    def next_high_impact(self) -> Event | None:
        for e in self.upcoming(hours=24 * 7):
            if e.impact == "High" and e.minutes_away > -15:
                return e
        return None

    def due_warnings(self, warn_min: int) -> list[Event]:
        """High-impact events starting within ``warn_min`` minutes (and not yet past)."""
        return [e for e in self.upcoming(hours=warn_min / 60.0 + 0.5) if e.impact == "High" and 0 < e.minutes_away <= warn_min]

    # demo support
    def set_demo(self) -> None:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        demo = [
            Event("Core CPI m/m", "USD", now + timedelta(minutes=47), "High", "0.3%", "0.2%"),
            Event("Unemployment Claims", "USD", now + timedelta(hours=5, minutes=12), "Medium", "225K", "219K"),
            Event("FOMC Member Waller Speaks", "USD", now + timedelta(hours=9), "Medium"),
            Event("Non-Farm Employment Change", "USD", now + timedelta(days=1, hours=3), "High", "150K", "142K"),
            Event("FOMC Statement & Federal Funds Rate", "USD", now + timedelta(days=2, hours=6), "High", "4.00%", "4.25%"),
            Event("FOMC Press Conference", "USD", now + timedelta(days=2, hours=6, minutes=30), "High"),
        ]
        with self._lock:
            self._events, self._ts = demo, time.time() + 10 ** 9
