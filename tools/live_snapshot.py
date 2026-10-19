"""
Build a REAL-DATA snapshot for the static terminal when the full server can't run
(e.g. publishing the page from a sandbox).  Inputs are plain files you fetched by
any means; the analysis itself runs through the normal pipeline components.

    python tools/live_snapshot.py --daily daily.csv --price 4172.8 --news news.json \
        --calendar calendar.json --tape tape.json --out snapshots/

daily.csv   : date,usd,xag,btc,eur   (one row per day, oldest or newest first)
news.json   : [{"title":..., "source":..., "published": <RFC822 or ISO>, "link":...}, ...]
calendar.json: ForexFactory-style items (title, country, date, impact, forecast, previous)
tape.json   : [{"name":"SILVER","price":57.5,"change_pct":0.7,"kind":"price"}, ...]

The output folder gets report.json, tape.json, calendar.json, signals.json in the
same shapes as the live API, plus "snapshot": true markers, so
tools/build_static_demo.py --from snapshots/ --snapshot can publish it.
"""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from market.analysis import analyze_technicals  # noqa: E402
from market.data import MarketData  # noqa: E402
from news.calendar import parse_events  # noqa: E402
from news.fetch import article_id, is_relevant  # noqa: E402
from news.sentiment import aggregate, apply_lexicon, is_high_impact  # noqa: E402
from news.store import Article, Store  # noqa: E402
from pipeline import Report  # noqa: E402
from signals.engine import build_signal  # noqa: E402
from signals.tracker import SignalTracker  # noqa: E402
from web.server import report_to_json, signals_json  # noqa: E402


def _parse_when(raw: str) -> datetime:
    raw = (raw or "").strip()
    for fn in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), parsedate_to_datetime,
               lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S")):
        try:
            dt = fn(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
    return datetime.now(timezone.utc)


def daily_bars(csv_path: Path, live_price: float, now: datetime) -> pd.DataFrame:
    rows = []
    with open(csv_path) as f:
        for r in csv.reader(f):
            if len(r) < 2 or not r[0] or not r[1]:
                continue
            try:
                rows.append((datetime.strptime(r[0], "%Y-%m-%d").replace(tzinfo=timezone.utc), float(r[1])))
            except ValueError:
                continue
    rows.sort()
    # drop weekend rows that merely repeat Friday's value
    rows = [(d, c) for d, c in rows if d.weekday() < 5]
    closes = [c for _, c in rows]
    dates = [d for d, _ in rows]
    # today's bar from the live price
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if dates and dates[-1] < today:
        dates.append(today)
        closes.append(live_price)
    else:
        closes[-1] = live_price
    out = []
    for i, (d, c) in enumerate(zip(dates, closes)):
        o = closes[i - 1] if i else c
        spread = abs(c - o) * 0.3 + c * 0.0015      # modest intraday range estimate
        out.append({"open": o, "high": max(o, c) + spread, "low": min(o, c) - spread, "close": c, "volume": 0.0})
    df = pd.DataFrame(out, index=pd.DatetimeIndex(dates))
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", required=True)
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--news", required=True)
    ap.add_argument("--calendar", default=None)
    ap.add_argument("--tape", default=None)
    ap.add_argument("--source", default="gold-api.com · open exchange-rate data (daily)")
    ap.add_argument("--half-life", type=float, default=36.0, help="news half-life in hours (weekend-friendly)")
    ap.add_argument("--lookback", type=float, default=96.0)
    ap.add_argument("--out", default=str(ROOT / "dist" / "snapshots"))
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    bars = daily_bars(Path(args.daily), args.price, now)
    data = MarketData(hourly=bars, daily=bars, source=args.source, fetched_at=now)
    tech = analyze_technicals(data)
    # on a daily series the "hourly" changes mean: 1 bar = 1 day, 24 bars ≈ 1 month, 120 bars ≈ 6 months
    closes = bars["close"]
    tech.change_1h_pct = (closes.iloc[-1] / closes.iloc[-2] - 1) * 100 if len(closes) > 1 else 0.0
    tech.change_24h_pct = (closes.iloc[-1] / closes.iloc[-6] - 1) * 100 if len(closes) > 6 else 0.0
    tech.change_7d_pct = (closes.iloc[-1] / closes.iloc[-22] - 1) * 100 if len(closes) > 22 else 0.0

    # news
    arts: list[Article] = []
    seen = set()
    for item in json.loads(Path(args.news).read_text()):
        title = (item.get("title") or "").strip()
        if not title or not is_relevant(title):
            continue
        aid = article_id(title)
        if aid in seen:
            continue
        seen.add(aid)
        arts.append(Article(id=aid, title=title, link=item.get("link", ""), source=item.get("source", "") or "", summary="",
                            published_at=_parse_when(item.get("published", ""))))
    arts = [a for a in arts if a.age_hours <= args.lookback]
    apply_lexicon(arts)
    for a in arts:
        a.high_impact = is_high_impact(a, settings.high_impact_terms)
    news = aggregate(arts, half_life_hours=args.half_life)

    sig = build_signal(tech, news, tech_weight=settings.tech_weight, news_weight=settings.news_weight,
                       atr_stop_mult=settings.atr_stop_mult, atr_target_mult=settings.atr_target_mult)

    events = []
    if args.calendar:
        events = parse_events(json.loads(Path(args.calendar).read_text()), settings.calendar_currencies)
        events = [e for e in events if e.minutes_away > -15]

    report = Report(data=data, tech=tech, news=news, signal=sig, briefing=None, generated_at=now, events=events)

    # a clean tracker: opens a call only if the real analysis justifies one
    tmp = Path(tempfile.mkdtemp()) / "snap.db"
    tracker = SignalTracker(Store(tmp), min_conf=settings.signal_min_conf)
    tracker.on_analysis(report)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = report_to_json(report, history=[], candles=220)
    rep["snapshot"] = True
    rep["snapshot_at"] = now.isoformat()
    rep["timeframe"] = "D1"
    rep["demo"] = False
    rep["signals"] = signals_json(tracker)
    (out / "report.json").write_text(json.dumps(rep))
    (out / "signals.json").write_text(json.dumps(rep["signals"]))
    (out / "calendar.json").write_text(json.dumps({"events": [e.to_dict() for e in events], "demo": False}))
    tape = json.loads(Path(args.tape).read_text()) if args.tape else []
    (out / "tape.json").write_text(json.dumps({"items": tape, "demo": False}))

    print(f"price {tech.price:,.2f} · tech {tech.score:+d} ({tech.trend_label}) · news {news.score:+d} from {news.n_articles} headlines"
          f" · signal {sig.bias} conf {sig.confidence}% · bars {len(bars)} · events {len(events)}")
    act = tracker.active()
    print("call:", f"{act.side} @ {act.entry:,.1f} SL {act.sl:,.1f} TP1 {act.tp1:,.1f} TP2 {act.tp2:,.1f}" if act else "none (confidence below threshold or neutral)")
    for a in news.drivers[:8]:
        print(f"  [{a.impact:+.1f}] {a.title[:90]} — {a.source} ({a.age_hours:.0f}h)")


if __name__ == "__main__":
    main()
