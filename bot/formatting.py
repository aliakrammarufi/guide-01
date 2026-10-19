"""
Telegram message formatting (HTML parse mode).
"""
from __future__ import annotations

from html import escape
from datetime import datetime, timezone

from market.analysis import TechSnapshot
from news.sentiment import NewsScore
from news.store import Article
from signals.engine import Signal

DISCLAIMER = "<i>Decision support only – not financial advice. No orders are placed by this bot.</i>"


def _pct(x: float) -> str:
    arrow = "▲" if x > 0 else ("▼" if x < 0 else "•")
    return f"{arrow} {x:+.2f}%"


def _lvl(x: float | None) -> str:
    return f"{x:,.1f}" if x is not None else "n/a"


def _bar(score: int, width: int = 10) -> str:
    """Tiny text gauge: ◼ filled from the centre outwards."""
    half = width // 2
    filled = int(round(abs(score) / 100 * half))
    if score >= 0:
        return "░" * half + "▓" * filled + "░" * (half - filled)
    return "░" * (half - filled) + "▓" * filled + "░" * half


def _age(a: Article) -> str:
    h = a.age_hours
    if h < 1:
        return f"{int(h * 60)}m"
    if h < 48:
        return f"{h:.0f}h"
    return f"{h / 24:.0f}d"


def _impact_icon(a: Article) -> str:
    if a.impact is None:
        return "⚪"
    if a.impact >= 1.2:
        return "🟢🟢"
    if a.impact >= 0.35:
        return "🟢"
    if a.impact <= -1.2:
        return "🔴🔴"
    if a.impact <= -0.35:
        return "🔴"
    return "⚪"


def headline_line(a: Article, with_link: bool = True) -> str:
    title = escape(a.title)
    if with_link and a.link:
        title = f'<a href="{escape(a.link, quote=True)}">{title}</a>'
    src = escape(a.source) if a.source else ""
    reason = f" <i>{escape(a.reason)}</i>" if a.reason and a.scored_by == "claude" else ""
    return f"{_impact_icon(a)} {title} <code>{src} · {_age(a)}</code>{reason}"


def format_price(tech: TechSnapshot, source: str, as_of: datetime) -> str:
    ts = as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🥇 <b>XAU/USD</b>  <b>{tech.price:,.2f}</b>\n"
        f"1h {_pct(tech.change_1h_pct)}   24h {_pct(tech.change_24h_pct)}   ~1w {_pct(tech.change_7d_pct)}\n"
        f"ATR(14) {tech.atr:.1f}  ·  RSI {tech.rsi:.0f}  ·  {escape(tech.trend_label)}\n"
        f"<code>{escape(source)} · bar {ts}</code>"
    )


def format_signal(sig: Signal, compact: bool = False) -> str:
    lines = [
        f"{sig.emoji} <b>{escape(sig.label)}</b>   confidence {sig.confidence}%",
        f"Combined {sig.combined:+d}  <code>{_bar(sig.combined)}</code>",
        f"Technical {sig.tech_score:+d} · News {sig.news_score:+d} · {escape(sig.agreement)}",
    ]
    if sig.direction != "flat":
        lines.append(
            f"Ref levels: entry ~{sig.price:,.1f} | stop {_lvl(sig.stop)} | "
            f"T1 {_lvl(sig.target1)} | T2 {_lvl(sig.target2)}"
        )
    lines.append(f"Support {_lvl(sig.nearest_support)} · Resistance {_lvl(sig.nearest_resistance)}")
    if not compact and sig.notes:
        lines.append("\n".join(f"• {escape(n)}" for n in sig.notes))
    return "\n".join(lines)


def format_technicals(tech: TechSnapshot) -> str:
    lines = [
        f"📈 <b>Technicals</b>  score {tech.score:+d}  ({escape(tech.trend_label)})",
        f"EMA20/50/200 (H1): {tech.ema20:,.1f} / {tech.ema50:,.1f} / {tech.ema200:,.1f}",
        f"Daily EMA50/200: {tech.daily_ema50:,.1f} / {tech.daily_ema200:,.1f}",
        f"RSI {tech.rsi:.1f} · MACD hist {tech.macd_hist:+.2f} · BB %B {tech.bb_pct:.2f}",
    ]
    if tech.pivots:
        p = tech.pivots
        lines.append(f"Pivots: S2 {p['S2']:,.0f} · S1 {p['S1']:,.0f} · P {p['P']:,.0f} · R1 {p['R1']:,.0f} · R2 {p['R2']:,.0f}")
    if tech.reasons:
        lines.append("\n".join(f"• {escape(r)}" for r in tech.reasons))
    if tech.flags:
        lines.append("\n".join(f"⚠️ {escape(f)}" for f in tech.flags))
    return "\n".join(lines)


def format_news(news: NewsScore, max_items: int = 8, hours: int = 36) -> str:
    head = (
        f"📰 <b>News</b>  score {news.score:+d}  ({escape(news.label)})\n"
        f"{news.n_bullish} bullish · {news.n_bearish} bearish · {news.n_neutral} neutral in last {hours}h"
    )
    if news.top_themes:
        head += "\nThemes: " + ", ".join(f"{escape(t)} {v:+.1f}" for t, v in news.top_themes)
    if not news.drivers:
        return head + "\n<i>No relevant headlines yet – the first news scan may still be running.</i>"
    items = [headline_line(a) for a in news.drivers[:max_items]]
    return head + "\n\n" + "\n".join(items)


def _countdown(minutes: float) -> str:
    if minutes < 0:
        return "now"
    if minutes < 60:
        return f"in {int(minutes)} min"
    if minutes < 48 * 60:
        h, m = divmod(int(minutes), 60)
        return f"in {h}h {m:02d}m"
    return f"in {minutes / 1440:.1f} days"


def format_calendar(events: list, limit: int = 8) -> str:
    head = "📅 <b>Upcoming releases</b> (USD, medium/high impact)"
    if not events:
        return head + "\n<i>Nothing scheduled in the next 48h (or the calendar is unreachable).</i>"
    lines = [head]
    for e in events[:limit]:
        flame = "🔥" if e.impact == "High" else "•"
        when = e.when.strftime("%a %H:%M UTC")
        extra = ""
        if e.forecast or e.previous:
            extra = f" <code>f {escape(e.forecast or '–')} · p {escape(e.previous or '–')}</code>"
        if e.actual:
            extra += f" → <b>{escape(e.actual)}</b>"
        lines.append(f"{flame} {escape(e.title)} — {when} ({_countdown(e.minutes_away)}){extra}")
    return "\n".join(lines)


def format_next_event(e) -> str:
    if e is None:
        return ""
    return f"⏱ Next high-impact: <b>{escape(e.title)}</b> {_countdown(e.minutes_away)} ({e.when.strftime('%a %H:%M UTC')})"


def format_alert_event(e) -> str:
    extra = f"\nForecast {escape(e.forecast or '–')} · previous {escape(e.previous or '–')}" if (e.forecast or e.previous) else ""
    return (
        f"⏰ <b>{escape(e.title)} {_countdown(e.minutes_away)}</b> ({e.when.strftime('%H:%M UTC')})\n"
        f"High-impact USD release – gold often moves sharply and spreads widen. "
        f"Consider tightening risk or waiting for the print.{extra}"
    )


def format_trade_signal(t, price: float | None = None) -> str:
    """One BUY/SELL call as a Telegram block."""
    icon = "🟢 BUY" if t.side == "BUY" else "🔴 SELL"
    status = {"active": "ACTIVE", "tp1": "TP1 HIT · stop at break-even", "tp2": "TP2 HIT ✅", "stopped": "STOPPED ❌",
              "flipped": "CLOSED (bias flipped)", "expired": "CLOSED (expired)"}.get(t.status, t.status.upper())
    lines = [
        f"{icon} <b>XAU/USD</b> · <b>{escape(status)}</b>",
        f"Entry <b>{t.entry:,.1f}</b> · SL <b>{t.effective_sl:,.1f}</b> · TP1 <b>{t.tp1:,.1f}</b> · TP2 <b>{t.tp2:,.1f}</b>",
        f"Risk {t.risk:,.1f} = 1R · R:R 1:{abs(t.tp2 - t.entry) / t.risk:.1f} · confidence {t.confidence}%",
    ]
    if t.is_open and price is not None:
        lines.append(f"Now {price:,.1f} → open <b>{(price - t.entry) * t.sign:+.1f}</b> ({t.r_at(price):+.2f}R)")
    if not t.is_open and t.result_r is not None:
        lines.append(f"Result <b>{t.result_r:+.2f}R</b> @ {t.exit_price:,.1f}")
    if t.reason:
        lines.append(f"<i>{escape(t.reason)}</i>")
    lines.append(f"<code>opened {t.opened_at.strftime('%d %b %H:%M UTC')}</code>")
    return "\n".join(lines)


def format_signals_overview(active, history: list, stats: dict, price: float | None) -> str:
    parts = ["📡 <b>Trade signals</b>"]
    parts.append(format_trade_signal(active, price) if active else "⚪ No open call right now – waiting for technicals and news to line up.")
    closed = [h for h in history if not h.is_open][:8]
    if closed:
        rows = []
        for h in closed:
            res = f"{h.result_r:+.2f}R" if h.result_r is not None else "–"
            mark = "✅" if (h.result_r or 0) > 0.05 else ("❌" if (h.result_r or 0) < -0.05 else "➖")
            rows.append(f"{mark} {h.side} {h.entry:,.0f} → {h.exit_price:,.0f} · {res} · {h.opened_at.strftime('%d %b')}")
        parts.append("<b>Recent</b>\n" + "\n".join(rows))
    if stats.get("count"):
        parts.append(f"Closed {stats['count']} · win rate {stats['win_rate']}% · total {stats['total_r']:+.2f}R · avg {stats['avg_r']:+.2f}R")
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def format_signal_event(e) -> str:
    s = e.signal
    head = {
        "opened": f"📡 <b>NEW {s.side} SIGNAL</b>",
        "tp1": "🎯 <b>TP1 HIT</b>",
        "tp2": "🏁 <b>TP2 HIT – closed</b>",
        "stopped": "🛑 <b>STOPPED OUT – closed</b>",
        "flipped": "🔁 <b>Closed – bias flipped</b>",
        "expired": "⌛ <b>Closed – max hold reached</b>",
    }.get(e.kind, e.kind)
    return f"{head}\n{escape(e.text)}\n\n{format_trade_signal(s, e.price if s.is_open else None)}"


def format_full_report(tech: TechSnapshot, news: NewsScore, sig: Signal, briefing: str | None,
                       source: str, as_of: datetime, max_headlines: int = 6, hours: int = 36,
                       events: list | None = None, trade=None) -> str:
    parts = [
        format_price(tech, source, as_of),
        format_signal(sig),
    ]
    if trade is not None:
        parts.append(format_trade_signal(trade, tech.price))
    nxt = next((e for e in (events or []) if e.impact == "High" and e.minutes_away > -15), None)
    if nxt is not None:
        parts.append(format_next_event(nxt))
    if briefing:
        parts.append(f"🧠 <b>AI briefing</b>\n{escape(briefing)}")
    parts.append(format_technicals(tech))
    parts.append(format_news(news, max_items=max_headlines, hours=hours))
    if events:
        parts.append(format_calendar(events, limit=5))
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def format_alert_bias_change(prev_bias: str | None, sig: Signal, tech: TechSnapshot) -> str:
    was = escape(prev_bias.replace("_", " ").title()) if prev_bias else "n/a"
    return (
        f"🔔 <b>Bias changed</b>: {was} → {sig.emoji} <b>{escape(sig.label)}</b>\n"
        f"XAU/USD {tech.price:,.2f}  ·  combined {sig.combined:+d}  ·  confidence {sig.confidence}%\n"
        f"{format_signal(sig, compact=True)}\n\n{DISCLAIMER}"
    )


def format_alert_news(articles: list[Article]) -> str:
    lines = ["🚨 <b>High-impact gold news</b>"]
    lines += [headline_line(a) for a in articles[:5]]
    return "\n".join(lines)


def format_alert_price(tech: TechSnapshot, move_pct: float, window_label: str) -> str:
    direction = "jumped" if move_pct > 0 else "dropped"
    return (
        f"⚡ <b>XAU/USD {direction} {abs(move_pct):.2f}% in the last {window_label}</b>\n"
        f"Now {tech.price:,.2f} · RSI {tech.rsi:.0f} · ATR {tech.atr:.1f}\n"
        f"Support {_lvl(tech.nearest_support())} · Resistance {_lvl(tech.nearest_resistance())}"
    )


HELP = """<b>XAU Sentinel – commands</b>
/price – current XAU/USD price & quick stats
/signal – bias, confidence and reference levels
/analyze – full report: signal, AI briefing, technicals, news
/news – latest gold-moving headlines with sentiment
/levels – support, resistance and pivot levels
/signals – open BUY/SELL call with SL, TP1, TP2 + recent results
/calendar – upcoming CPI / FOMC / payrolls and other USD releases
/subscribe – receive the scheduled report in this chat
/unsubscribe – stop scheduled reports
/alerts on|off – bias-change, big-move, breaking-news and pre-release alerts
/status – bot status and settings
/help – this message

""" + DISCLAIMER
