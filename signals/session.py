"""
Trading-session awareness for gold.

Gold's behaviour depends heavily on the time of day: the London open and the
London/New York overlap carry most of the volume and the clean moves; the
Asian session is thin and choppy; the hour or two after the New York close is
dead; Friday evenings wind down and the Sunday open can gap.

``session_info`` turns "now" (plus the economic calendar) into a small
verdict the signal engine and the tracker use:

* ``quality``        0..1   how good this hour is for a new entry
* ``allow_entries``  bool   whether a NEW call may be opened right now
* ``conf_adj``       int    confidence adjustment (negative in thin hours)
* ``reasons``        why    (shown to the user)

Times are UTC. Daylight-saving shifts move the real sessions by an hour; the
windows below are deliberately a little wide so they stay right year-round.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class SessionInfo:
    name: str                   # Asia | London | London/NY overlap | New York | Dead zone | Weekend | Sunday open | Friday close
    quality: float              # 0..1
    allow_entries: bool
    conf_adj: int               # added to the signal confidence
    reasons: list[str] = field(default_factory=list)
    blackout: bool = False      # inside a high-impact news window
    blackout_event: str = ""
    next_change: datetime | None = None   # when the next session starts
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def label(self) -> str:
        if self.blackout:
            return f"News blackout · {self.blackout_event}"
        q = "best" if self.quality >= 0.95 else "good" if self.quality >= 0.8 else "ok" if self.quality >= 0.65 else "thin" if self.quality > 0 else "closed"
        return f"{self.name} · {q}"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "label": self.label, "quality": round(self.quality, 2),
            "allow_entries": self.allow_entries, "conf_adj": self.conf_adj, "reasons": self.reasons,
            "blackout": self.blackout, "blackout_event": self.blackout_event,
            "next_change": self.next_change.isoformat() if self.next_change else None,
        }


# (start_hour, end_hour, name, quality, conf_adj) in UTC, Monday-Friday
WINDOWS = [
    (0, 7, "Asia", 0.5, -10),
    (7, 12, "London", 0.85, 0),
    (12, 16, "London/NY overlap", 1.0, +5),
    (16, 21, "New York", 0.7, -5),
    (21, 23, "Dead zone", 0.0, -20),
    (23, 24, "Asia", 0.5, -10),
]


def _window(hour: int):
    for start, end, name, quality, adj in WINDOWS:
        if start <= hour < end:
            return start, end, name, quality, adj
    return 0, 24, "Asia", 0.5, -10


def session_info(now: datetime | None = None, events: list | None = None, *,
                 asia_entries: bool = True, blackout_before_min: int = 30, blackout_after_min: int = 15,
                 blocked: tuple[str, ...] | list[str] | None = None) -> SessionInfo:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wd, hour = now.weekday(), now.hour      # Monday = 0
    reasons: list[str] = []

    # ---- weekend / Sunday open / Friday close -------------------------------
    if wd == 5 or (wd == 6 and hour < 22):
        nxt = (now + timedelta(days=(6 - wd))).replace(hour=22, minute=0, second=0, microsecond=0)
        return SessionInfo("Weekend", 0.0, False, -30, ["Market closed"], next_change=nxt, now=now)
    if wd == 6 or (wd == 0 and hour < 1):
        nxt = (now + timedelta(days=1 if wd == 6 else 0)).replace(hour=1, minute=0, second=0, microsecond=0)
        return SessionInfo("Sunday open", 0.2, False, -20,
                           ["Sunday open – gap risk and wide spreads, no new entries until 01:00 UTC"], next_change=nxt, now=now)
    if wd == 4 and hour >= 19:
        nxt = (now + timedelta(days=2)).replace(hour=22, minute=0, second=0, microsecond=0)
        return SessionInfo("Friday close", 0.3, False, -15,
                           ["Friday evening – liquidity drains into the close, no new entries over the weekend"], next_change=nxt, now=now)

    # ---- regular sessions ---------------------------------------------------
    start, end, name, quality, adj = _window(hour)
    allow = quality > 0
    if name == "Asia":
        reasons.append("Asian session – thin and choppy, size down and demand a clean confirmation")
        if not asia_entries:
            allow = False
            reasons.append("Asian-session entries are switched off")
    elif name == "Dead zone":
        reasons.append("New York close → Sydney open: no liquidity, no new entries")
    elif name == "London/NY overlap":
        reasons.append("London/New York overlap – the most liquid hours for gold")
    elif name == "London":
        reasons.append("London session – good liquidity")
    elif name == "New York":
        reasons.append("New York afternoon – decent liquidity, fades into the close")
    if blocked and name in blocked:
        allow = False
        reasons.append(f"{name} entries are switched off (BLOCKED_SESSIONS)")
    next_change = now.replace(hour=end % 24, minute=0, second=0, microsecond=0)
    if end >= 24 or next_change <= now:
        next_change += timedelta(days=1)

    info = SessionInfo(name, quality, allow, adj, reasons, next_change=next_change, now=now)

    # ---- high-impact news blackout -----------------------------------------
    for e in events or []:
        if getattr(e, "impact", "") != "High":
            continue
        mins = e.minutes_away
        if -blackout_after_min <= mins <= blackout_before_min:
            info.blackout = True
            info.blackout_event = e.title
            info.allow_entries = False
            info.conf_adj -= 15
            when = "in a few minutes" if mins > 0 else "just released"
            info.reasons.insert(0, f"{e.title} {when} – no new entries from {blackout_before_min} min before to {blackout_after_min} min after")
            break
    return info
