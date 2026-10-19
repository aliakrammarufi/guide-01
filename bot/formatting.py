"""
Telegram message formatting (HTML parse mode).

Style rules, so every message reads the same way on a phone:
* one title line with an emoji and the thing that matters most, in bold
* one fact per line – label first, value in bold – never three facts crammed on a line
* short section headers for long messages, separated by a thin rule
* numbers formatted for people (4,180.80 · +18.5 · 0.8R), not for logs
* a single short footer: when the bar closed, which data, and the no-orders note
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from market.analysis import TechSnapshot
from news.sentiment import NewsScore
from news.store import Article
from signals.engine import Signal

DISCLAIMER = "<i>ℹ️ Decision support only — this bot never places orders.</i>"
RULE = "──────────────"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _pct(x: float, label: str = "") -> str:
    arrow = "▲" if x > 0.005 else ("▼" if x < -0.005 else "•")
    s = f"{arrow} {x:+.2f}%".replace("-", "−")
    return f"{s} {label}".strip()


def _num(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x:,.{d}f}"


def _diff(x: float, d: int = 1) -> str:
    return f"{x:+,.{d}f}".replace("-", "−")


def _r(x: float, d: int = 2) -> str:
    return f"{x:+.{d}f}R".replace("-", "−")


def _meter(score: int, width: int = 10) -> str:
    """Bearish ◁ ▰▰▰▰▰▱▱▱▱▱ ▷ Bullish – the filled part shows where the score sits."""
    pos = max(0, min(width, round((score + 100) / 200 * width)))
    return "▰" * pos + "▱" * (width - pos)


def _confidence_word(c: int) -> str:
    if c >= 70:
        return "strong"
    if c >= 45:
        return "good"
    if c >= 25:
        return "moderate"
    return "weak"


def _score_word(s: int) -> str:
    a = abs(s)
    if a >= 55:
        tone = "strongly"
    elif a >= 25:
        tone = "clearly"
    elif a >= 10:
        tone = "slightly"
    else:
        return "neutral"
    return f"{tone} {'bullish' if s > 0 else 'bearish'}"


def _source_label(source: str) -> str:
    s = (source or "").lower()
    if "synthetic" in s or "demo" in s:
        return "demo data"
    adj = " · spot-adjusted" if "→spot" in s else ""
    if s.startswith("yfinance"):
        sym = source.split(":", 1)[1].replace("→spot", "") if ":" in source else ""
        return f"Yahoo Finance {sym}{adj}".strip()
    if s.startswith("twelvedata"):
        return "Twelve Data spot"
    return escape(source)


def _footer(source: str, as_of: datetime, with_disclaimer: bool = True) -> str:
    ts = as_of.astimezone(timezone.utc).strftime("%H:%M UTC · %d %b")
    line = f"<i>🕒 Last bar {ts} · {_source_label(source)}</i>"
    return line + ("\n" + DISCLAIMER if with_disclaimer else "")


def _age(a: Article) -> str:
    h = a.age_hours
    if h < 1:
        return f"{int(h * 60)} min ago"
    if h < 48:
        return f"{h:.0f}h ago"
    return f"{h / 24:.0f}d ago"


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
    meta = " · ".join(x for x in (escape(a.source) if a.source else "", _age(a)) if x)
    reason = f"\n      <i>{escape(a.reason)}</i>" if a.reason and a.scored_by == "claude" else ""
    return f"{_impact_icon(a)} {title}\n      <i>{meta}</i>{reason}"


# ---------------------------------------------------------------------------
# price
# ---------------------------------------------------------------------------
def price_header(tech: TechSnapshot) -> str:
    return (
        f"🥇 <b>XAU/USD   {tech.price:,.2f}</b>\n"
        f"{_pct(tech.change_1h_pct, '1h')}   ·   {_pct(tech.change_24h_pct, '24h')}   ·   {_pct(tech.change_7d_pct, '1w')}"
    )


def format_price(tech: TechSnapshot, source: str, as_of: datetime) -> str:
    return (
        f"{price_header(tech)}\n\n"
        f"📊 Trend: <b>{escape(tech.trend_label)}</b>\n"
        f"RSI (14): <b>{tech.rsi:.0f}</b>   ·   ATR (14): <b>{tech.atr:,.1f}</b>\n"
        f"🧱 Support <b>{_num(tech.nearest_support())}</b>   ·   Resistance <b>{_num(tech.nearest_resistance())}</b>\n\n"
        f"{_footer(source, as_of, with_disclaimer=False)}"
    )


# ---------------------------------------------------------------------------
# signal (bias)
# ---------------------------------------------------------------------------
def format_signal(sig: Signal, compact: bool = False) -> str:
    conf = f"{sig.confidence}% — {_confidence_word(sig.confidence)}"
    lines = [
        f"{sig.emoji} <b>{escape(sig.label.upper())}</b>",
        f"Confidence: <b>{conf}</b>",
        f"Bearish ◁ {_meter(sig.combined)} ▷ Bullish   <b>{_diff(sig.combined, 0)}</b>",
        "",
        f"Technicals: <b>{_diff(sig.tech_score, 0)}</b> · {_score_word(sig.tech_score)}",
        f"News flow: <b>{_diff(sig.news_score, 0)}</b> · {_score_word(sig.news_score)}",
    ]
    agree = {"aligned": "✅ aligned — technicals and news point the same way",
             "conflict": "⚠️ conflict — technicals and news disagree",
             "one-sided": "➖ one-sided — only one of them has a view"}.get(sig.agreement, escape(sig.agreement))
    lines.append(f"Agreement: {agree}")

    if sig.direction != "flat" and sig.stop is not None:
        side = "long" if sig.direction == "long" else "short"
        lines += [
            "",
            f"🎯 <b>Reference levels</b> ({side})",
            f"Entry        ~<b>{sig.price:,.1f}</b>",
            f"Stop          <b>{_num(sig.stop)}</b>   ({_diff(sig.stop - sig.price)})",
            f"Target 1    <b>{_num(sig.target1)}</b>   ({_diff(sig.target1 - sig.price)})",
            f"Target 2    <b>{_num(sig.target2)}</b>   ({_diff(sig.target2 - sig.price)})",
        ]
        risk = abs(sig.price - sig.stop)
        if risk:
            lines.append(f"Reward : risk   <b>1 : {abs(sig.target2 - sig.price) / risk:.1f}</b>")
    lines += ["", f"🧭 Support <b>{_num(sig.nearest_support)}</b>   ·   Resistance <b>{_num(sig.nearest_resistance)}</b>"]
    if not compact and sig.notes:
        lines += ["", "\n".join(f"💡 {escape(n)}" for n in sig.notes)]
    return "\n".join(lines)


def format_signal_message(tech: TechSnapshot, sig: Signal, source: str, as_of: datetime) -> str:
    """/signal – price line, the bias block and a footer."""
    return f"{price_header(tech)}\n\n{format_signal(sig)}\n\n{_footer(source, as_of)}"


# ---------------------------------------------------------------------------
# technicals / levels
# ---------------------------------------------------------------------------
def format_technicals(tech: TechSnapshot) -> str:
    lines = [
        f"📈 <b>Technicals</b>   score <b>{_diff(tech.score, 0)}</b> · {escape(tech.trend_label)}",
        f"EMA 20 / 50 / 200 (H1): <b>{tech.ema20:,.1f}</b> / <b>{tech.ema50:,.1f}</b> / <b>{tech.ema200:,.1f}</b>",
        f"Daily EMA 50 / 200: <b>{tech.daily_ema50:,.1f}</b> / <b>{tech.daily_ema200:,.1f}</b>",
        f"RSI <b>{tech.rsi:.1f}</b>   ·   MACD hist <b>{_diff(tech.macd_hist, 2)}</b>   ·   Bollinger %B <b>{tech.bb_pct:.2f}</b>",
    ]
    if tech.pivots:
        p = tech.pivots
        lines.append(f"Pivots: S2 {p['S2']:,.0f} · S1 {p['S1']:,.0f} · <b>P {p['P']:,.0f}</b> · R1 {p['R1']:,.0f} · R2 {p['R2']:,.0f}")
    if tech.reasons:
        lines.append("\n".join(f"• {escape(r)}" for r in tech.reasons))
    if tech.flags:
        lines.append("\n".join(f"⚠️ {escape(f)}" for f in tech.flags))
    return "\n".join(lines)


def format_levels(tech: TechSnapshot) -> str:
    res = sorted(r for r in tech.resistances if r > tech.price)[:4]
    sup = sorted((s for s in tech.supports if s < tech.price), reverse=True)[:4]
    lines = [f"📐 <b>Key levels</b>   price <b>{tech.price:,.2f}</b> · ATR {tech.atr:,.1f}", ""]
    lines.append("<b>Resistance</b> (above)")
    lines += [f"  🔺 <b>{r:,.1f}</b>   ({_diff(r - tech.price)})" for r in reversed(res)] or ["  none nearby"]
    lines.append(f"  ━━ now <b>{tech.price:,.2f}</b> ━━")
    lines.append("<b>Support</b> (below)")
    lines += [f"  🔻 <b>{s:,.1f}</b>   ({_diff(s - tech.price)})" for s in sup] or ["  none nearby"]
    if tech.pivots:
        p = tech.pivots
        lines += ["", "<b>Daily pivots</b>", f"R2 {p['R2']:,.1f} · R1 {p['R1']:,.1f} · <b>P {p['P']:,.1f}</b> · S1 {p['S1']:,.1f} · S2 {p['S2']:,.1f}"]
    lines += ["", f"EMA 20 / 50 / 200 (H1): {tech.ema20:,.1f} / {tech.ema50:,.1f} / {tech.ema200:,.1f}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# news
# ---------------------------------------------------------------------------
def format_news(news: NewsScore, max_items: int = 8, hours: int = 36) -> str:
    head = (
        f"📰 <b>News flow</b>   score <b>{_diff(news.score, 0)}</b> · {escape(news.label)}\n"
        f"🟢 {news.n_bullish} bullish   🔴 {news.n_bearish} bearish   ⚪ {news.n_neutral} neutral   <i>(last {hours}h)</i>"
    )
    if news.top_themes:
        head += "\nThemes: " + " · ".join(f"{escape(t)} <b>{v:+.1f}</b>" for t, v in news.top_themes)
    if not news.drivers:
        return head + "\n\n<i>No relevant headlines yet – the first scan may still be running.</i>"
    items = [headline_line(a) for a in news.drivers[:max_items]]
    return head + "\n\n" + "\n".join(items)


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------
def _countdown(minutes: float) -> str:
    if minutes < 0:
        return "now"
    if minutes < 60:
        return f"in {int(minutes)} min"
    if minutes < 48 * 60:
        h, m = divmod(int(minutes), 60)
        return f"in {h}h {m:02d}m"
    return f"in {minutes / 1440:.1f} days"


def _event_line(e, show_day: bool = True) -> str:
    flame = "🔥" if e.impact == "High" else "🔸"
    when = e.when.strftime("%a %H:%M UTC") if show_day else e.when.strftime("%H:%M UTC")
    line = f"{flame} <b>{escape(e.title)}</b>\n      {when} · {_countdown(e.minutes_away)}"
    bits = []
    if e.forecast:
        bits.append(f"forecast {escape(e.forecast)}")
    if e.previous:
        bits.append(f"previous {escape(e.previous)}")
    if e.actual:
        bits.append(f"actual <b>{escape(e.actual)}</b>")
    if bits:
        line += "\n      <i>" + " · ".join(bits) + "</i>"
    return line


def format_calendar(events: list, limit: int = 8) -> str:
    head = "📅 <b>Upcoming USD releases</b>   <i>🔥 high impact · 🔸 medium</i>"
    if not events:
        return head + "\n\n<i>Nothing scheduled in the next 48h (or the calendar is unreachable).</i>"
    return head + "\n\n" + "\n".join(_event_line(e) for e in events[:limit])


def format_next_event(e) -> str:
    if e is None:
        return ""
    return f"⏱ Next high-impact release: <b>{escape(e.title)}</b> {_countdown(e.minutes_away)} ({e.when.strftime('%a %H:%M UTC')})"


def format_alert_event(e) -> str:
    extra = ""
    if e.forecast or e.previous:
        extra = f"\nForecast <b>{escape(e.forecast or '–')}</b> · previous <b>{escape(e.previous or '–')}</b>"
    return (
        f"⏰ <b>{escape(e.title)}</b> {_countdown(e.minutes_away)}   ({e.when.strftime('%H:%M UTC')})\n"
        f"High-impact USD release. Gold often moves sharply and spreads widen – "
        f"consider tightening risk or waiting for the print.{extra}"
    )


# ---------------------------------------------------------------------------
# trade signals
# ---------------------------------------------------------------------------
def format_trade_signal(t, price: float | None = None) -> str:
    """One BUY/SELL call as a clean block."""
    side = "🟢 <b>BUY</b>" if t.side == "BUY" else "🔴 <b>SELL</b>"
    status = {"active": "ACTIVE", "tp1": "TP1 HIT · stop moved to break-even", "tp2": "TP2 HIT ✅",
              "stopped": "STOPPED OUT ❌", "flipped": "CLOSED · bias flipped", "expired": "CLOSED · max hold reached"}.get(t.status, t.status.upper())
    sgn = t.sign
    rr = abs(t.tp2 - t.entry) / t.risk if t.risk else 0
    lines = [
        f"{side} XAU/USD   —   <b>{escape(status)}</b>",
        f"Entry            <b>{t.entry:,.1f}</b>",
        f"Stop loss      <b>{t.effective_sl:,.1f}</b>   ({_diff((t.effective_sl - t.entry))} · {_r((t.effective_sl - t.entry) * sgn / t.risk, 1)})" if t.risk else f"Stop loss      <b>{t.effective_sl:,.1f}</b>",
        f"Take profit 1  <b>{t.tp1:,.1f}</b>   ({_diff(t.tp1 - t.entry)} · {abs(t.tp1 - t.entry) / t.risk:.1f}R)" if t.risk else f"Take profit 1  <b>{t.tp1:,.1f}</b>",
        f"Take profit 2  <b>{t.tp2:,.1f}</b>   ({_diff(t.tp2 - t.entry)} · {rr:.1f}R)",
        f"Reward : risk   <b>1 : {rr:.1f}</b>   ·   confidence <b>{t.confidence}%</b>",
    ]
    if t.is_open and price is not None:
        pnl = (price - t.entry) * sgn
        lines.append(f"📍 Now <b>{price:,.1f}</b> → open <b>{_diff(pnl)}</b> ({_r(t.r_at(price))})")
    if not t.is_open and t.result_r is not None:
        mark = "✅" if t.result_r > 0.05 else ("❌" if t.result_r < -0.05 else "➖")
        lines.append(f"{mark} Result <b>{_r(t.result_r)}</b> at {t.exit_price:,.1f}")
    if t.reason:
        lines.append(f"<i>{escape(t.reason)}</i>")
    lines.append(f"<i>Opened {t.opened_at.strftime('%d %b %H:%M UTC')}</i>")
    return "\n".join(lines)


def format_signals_overview(active, history: list, stats: dict, price: float | None) -> str:
    parts = ["📡 <b>Trade signals</b>"]
    if active:
        parts.append(format_trade_signal(active, price))
    else:
        parts.append("⚪ <b>No open call right now.</b>\nA BUY or SELL is posted here the moment technicals and news line up with enough confidence.")
    closed = [h for h in history if not h.is_open][:8]
    if closed:
        rows = []
        for h in closed:
            res = _r(h.result_r) if h.result_r is not None else "–"
            mark = "✅" if (h.result_r or 0) > 0.05 else ("❌" if (h.result_r or 0) < -0.05 else "➖")
            rows.append(f"{mark} {h.side} {h.entry:,.0f} → {h.exit_price:,.0f}   <b>{res}</b>   <i>{h.opened_at.strftime('%d %b')}</i>")
        parts.append("<b>Recent calls</b>\n" + "\n".join(rows))
    if stats.get("count"):
        parts.append(f"📊 Closed <b>{stats['count']}</b> · win rate <b>{stats['win_rate']}%</b> · total <b>{_r(stats['total_r'])}</b> · average <b>{_r(stats['avg_r'])}</b>")
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def format_signal_event(e) -> str:
    s = e.signal
    head = {
        "opened": f"📡 <b>NEW {s.side} SIGNAL</b>",
        "tp1": "🎯 <b>TAKE PROFIT 1 HIT</b>",
        "tp2": "🏁 <b>TAKE PROFIT 2 HIT — closed</b>",
        "stopped": "🛑 <b>STOPPED OUT — closed</b>",
        "flipped": "🔁 <b>CLOSED — bias flipped</b>",
        "expired": "⌛ <b>CLOSED — max hold reached</b>",
    }.get(e.kind, e.kind)
    return f"{head}\n{escape(e.text)}\n\n{format_trade_signal(s, e.price if s.is_open else None)}\n\n{DISCLAIMER}"


# ---------------------------------------------------------------------------
# full report
# ---------------------------------------------------------------------------
def format_full_report(tech: TechSnapshot, news: NewsScore, sig: Signal, briefing: str | None,
                       source: str, as_of: datetime, max_headlines: int = 6, hours: int = 36,
                       events: list | None = None, trade=None) -> str:
    sections = [price_header(tech), format_signal(sig)]
    if trade is not None:
        sections.append(format_trade_signal(trade, tech.price))
    nxt = next((e for e in (events or []) if e.impact == "High" and e.minutes_away > -15), None)
    if nxt is not None:
        sections.append(format_next_event(nxt))
    if briefing:
        sections.append(f"🧠 <b>AI briefing</b>\n{escape(briefing)}")
    sections.append(format_technicals(tech))
    sections.append(format_news(news, max_items=max_headlines, hours=hours))
    if events:
        sections.append(format_calendar(events, limit=5))
    sections.append(_footer(source, as_of))
    return f"\n\n{RULE}\n\n".join(sections)


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------
def format_alert_bias_change(prev_bias: str | None, sig: Signal, tech: TechSnapshot) -> str:
    was = escape(prev_bias.replace("_", " ").title()) if prev_bias else "n/a"
    return (
        f"🔔 <b>Bias changed:</b> {was} → {sig.emoji} <b>{escape(sig.label)}</b>\n"
        f"XAU/USD <b>{tech.price:,.2f}</b>\n\n{format_signal(sig, compact=True)}\n\n{DISCLAIMER}"
    )


def format_alert_news(articles: list[Article]) -> str:
    lines = ["🚨 <b>High-impact gold news</b>", ""]
    lines += [headline_line(a) for a in articles[:5]]
    return "\n".join(lines)


def format_alert_price(tech: TechSnapshot, move_pct: float, window_label: str) -> str:
    direction = "jumped" if move_pct > 0 else "dropped"
    icon = "🚀" if move_pct > 0 else "📉"
    return (
        f"{icon} <b>XAU/USD {direction} {abs(move_pct):.2f}% in the last {window_label}</b>\n"
        f"Now <b>{tech.price:,.2f}</b>   ·   RSI {tech.rsi:.0f}   ·   ATR {tech.atr:,.1f}\n"
        f"🧱 Support <b>{_num(tech.nearest_support())}</b>   ·   Resistance <b>{_num(tech.nearest_resistance())}</b>"
    )


# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
HELP = """📖 <b>Commands</b>

<b>Market</b>
/price — live price, trend, RSI, ATR, nearest levels
/levels — support, resistance and daily pivots
/news — latest gold-moving headlines with sentiment
/calendar — upcoming CPI, FOMC, payrolls and other USD releases

<b>Signals</b>
/signal — current bias, confidence and reference levels
/signals — open BUY / SELL call with SL, TP1, TP2 and recent results
/analyze — the full report with chart, AI briefing, technicals and news

<b>Settings</b>
/subscribe · /unsubscribe — scheduled reports in this chat
/alerts on · /alerts off — bias changes, big moves, breaking news, pre-release warnings
/status — bot status
/help — this list

""" + DISCLAIMER
