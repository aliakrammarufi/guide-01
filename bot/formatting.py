"""
Telegram message formatting (HTML parse mode).

One layout for every message so the chat reads like a clean terminal:

  TITLE LINE        – bold, with the one number that matters
  section header    – emoji + bold caps
  table             – numbers in an aligned monospace block (<pre>), never
                      padded with spaces in proportional text
  one-line notes    – plain sentences, no decoration
  footer            – small italics: data time/source, the no-orders note

Rules: one fact per line, short labels (Entry / Stop / TP 1 / TP 2 / R:R),
proper minus signs, no log-style strings, a single short disclaimer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from market.analysis import TechSnapshot
from news.sentiment import NewsScore
from news.store import Article
from signals.engine import Signal
from signals.sizing import SYMBOLS, Sizing

DISCLAIMER = "<i>Decision support only — this bot never places orders.</i>"
RULE = "────────────────"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _m(x: str) -> str:
    """Typographic minus."""
    return x.replace("-", "−")


def _pct(x: float, label: str = "") -> str:
    arrow = "▲" if x > 0.005 else ("▼" if x < -0.005 else "•")
    return f"{arrow} {_m(f'{x:+.2f}')}% {label}".strip()


def _num(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x:,.{d}f}"


def _diff(x: float, d: int = 1) -> str:
    return _m(f"{x:+,.{d}f}")


def _r(x: float, d: int = 2) -> str:
    return _m(f"{x:+.{d}f}R")


def _pre(rows: list[tuple], right: tuple[int, ...] = (1,)) -> str:
    """Aligned monospace table. ``right`` = indexes of right-aligned columns."""
    rows = [tuple("" if c is None else str(c) for c in r) for r in rows]
    ncol = max(len(r) for r in rows)
    rows = [r + ("",) * (ncol - len(r)) for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(ncol)]
    lines = []
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            cells.append(c.rjust(widths[i]) if i in right else c.ljust(widths[i]))
        lines.append("  ".join(cells).rstrip())
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


def _meter(score: int, width: int = 10) -> str:
    pos = max(0, min(width, round((score + 100) / 200 * width)))
    return "▰" * pos + "▱" * (width - pos)


def _confidence_word(c: int) -> str:
    return "strong" if c >= 70 else "good" if c >= 45 else "moderate" if c >= 25 else "weak"


def _score_word(s: int) -> str:
    a = abs(s)
    if a < 10:
        return "neutral"
    tone = "strongly" if a >= 55 else "clearly" if a >= 25 else "slightly"
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
    line = f"<i>Last bar {ts} · {_source_label(source)}</i>"
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
    reason = f"\n     <i>{escape(a.reason)}</i>" if a.reason and a.scored_by == "claude" else ""
    return f"{_impact_icon(a)} {title}\n     <i>{meta}</i>{reason}"


def session_line(session: dict | None) -> str:
    if not session:
        return ""
    label = escape(session.get("label", ""))
    if session.get("blackout"):
        return f"🕑 <b>{label}</b> — no new entries"
    return f"🕑 <b>{label}</b>" + ("" if session.get("allow_entries", True) else " — no new entries right now")


def _sizing_row(sz: Sizing | None) -> tuple | None:
    if sz is None:
        return None
    return ("Size", f"{sz.lots:.2f} lots", f"risk {sz.money(sz.actual_risk)} ({sz.pct_of_account:.1f}%)")


def sizing_note(sz: Sizing | None, entry: float, tp1: float, tp2: float) -> str:
    """One line under the table: what the size means in money, plus a warning when a fixed size risks too much."""
    if sz is None:
        return ""
    line = (f"At {sz.lots:.2f} lots: TP 1 ≈ <b>+{sz.money(sz.profit_at(tp1 - entry))}</b> · TP 2 ≈ <b>+{sz.money(sz.profit_at(tp2 - entry))}</b> · "
            f"stop −{sz.money(sz.actual_risk)} ({sz.pct_of_account:.1f}% of {sz.money(sz.account)})")
    if sz.over_guide and sz.is_fixed and sz.lots_by_risk < sz.lots:
        line += f"\n⚠️ Wide stop: {sz.lots:.2f} lots risks {sz.pct_of_account:.1f}% here — <b>{sz.lots_by_risk:.2f} lots</b> keeps it at {sz.risk_pct:g}%."
    elif sz.over_guide:
        line += f"\n⚠️ Even the minimum 0.01 lot risks {sz.pct_of_account:.1f}% on this stop — above your {sz.risk_pct:g}% guide. Consider skipping it."
    return line


def format_risk_settings(account: float | None, risk_pct: float | None, currency: str = "USD", fixed_lots: float = 0.0) -> str:
    sym = SYMBOLS.get((currency or "USD").upper(), currency + " ")
    if not account:
        return ("💼 <b>POSITION SIZING — OFF</b>\n"
                "Send /risk and answer with your account size and the % you risk per trade "
                "(for example <code>5000 1</code>), or type it in one go: <code>/risk 5000 1</code>.\n"
                "Every setup and live call will then include the lot size for its stop.")
    rows = [("Account", f"{sym}{account:,.0f}"), ("Risk guide", f"{risk_pct:g}% = {sym}{account * risk_pct / 100:,.0f} per trade")]
    if fixed_lots:
        rows.append(("Size", f"fixed {fixed_lots:.2f} lots"))
    else:
        rows.append(("Size", f"by risk ({risk_pct:g}% of account)"))
    rows.append(("Contract", "1 lot = 100 oz"))
    how = (f"Every call shows what {fixed_lots:.2f} lots risks on its stop, in {currency.upper()} and as a % of your account, "
           f"and warns when a wide stop pushes it past your {risk_pct:g}% guide."
           if fixed_lots else "Lots are sized so hitting the stop loses about the risk guide.")
    return ("💼 <b>POSITION SIZING</b>\n" + _pre(rows, right=()) + "\n" + how +
            "\nChange: <code>/risk 10000 1</code> · <code>/risk lots 0.05</code> · <code>/risk lots 0</code> (back to risk-based) · <code>/risk off</code>")


# ---------------------------------------------------------------------------
# price
# ---------------------------------------------------------------------------
def price_header(tech: TechSnapshot) -> str:
    return (f"🥇 <b>XAU/USD  {tech.price:,.2f}</b>\n"
            f"{_pct(tech.change_1h_pct, '1h')} · {_pct(tech.change_24h_pct, '24h')} · {_pct(tech.change_7d_pct, '1w')}")


def format_price(tech: TechSnapshot, source: str, as_of: datetime) -> str:
    rows = [
        ("Trend", tech.trend_label),
        ("RSI (14)", f"{tech.rsi:.0f}"),
        ("ATR (14)", f"{tech.atr:,.1f}"),
        ("Support", _num(tech.nearest_support())),
        ("Resistance", _num(tech.nearest_resistance())),
    ]
    return f"{price_header(tech)}\n\n📊 <b>MARKET</b>\n{_pre(rows, right=())}\n{_footer(source, as_of, with_disclaimer=False)}"


# ---------------------------------------------------------------------------
# signal (bias + plan)
# ---------------------------------------------------------------------------
def format_signal(sig: Signal, compact: bool = False) -> str:
    parts = [
        f"{sig.emoji} <b>{escape(sig.label.upper())}</b> — confidence <b>{sig.confidence}%</b> ({_confidence_word(sig.confidence)})",
        f"Bearish {_meter(sig.combined)} Bullish  <b>{_m(f'{sig.combined:+d}')}</b>",
    ]
    agree = {"aligned": "aligned ✅", "conflict": "conflict ⚠️", "one-sided": "one-sided"}.get(sig.agreement, sig.agreement)
    parts.append(_pre([
        ("Technicals", _m(f"{sig.tech_score:+d}"), _score_word(sig.tech_score)),
        ("News", _m(f"{sig.news_score:+d}"), _score_word(sig.news_score)),
        ("Agreement", "", agree),
    ]))

    if sig.direction != "flat" and sig.stop is not None and sig.zone_lo is not None:
        side = "BUY" if sig.direction == "long" else "SELL"
        mid = sig.zone_mid
        rows = [
            ("Stop", _num(sig.stop), _diff(sig.stop - mid)),
            ("TP 1", _num(sig.target1), _diff(sig.target1 - mid)),
            ("TP 2", _num(sig.target2), _diff(sig.target2 - mid)),
            ("R:R", f"1 : {sig.planned_rr or 0:.1f}", ""),
        ]
        how = ("Price is in the zone — wait for a 15-minute confirmation."
               if sig.entry_mode == "in-zone" else
               f"Wait for a pullback into the zone. Don't chase beyond {_num(sig.chase_limit)}.")
        conf = confluence_line(sig.confluence)
        parts.append(f"🎯 <b>{side} PLAN</b> — entry zone <b>{_num(sig.zone_lo)} – {_num(sig.zone_hi)}</b>\n{_pre(rows)}\n{how}"
                     + (f"\n{conf}" if conf else ""))

    tail = []
    sl = session_line(sig.session)
    if sl:
        tail.append(sl)
    tail.append(f"🧭 Support <b>{_num(sig.nearest_support)}</b> · Resistance <b>{_num(sig.nearest_resistance)}</b>")
    parts.append("\n".join(tail))
    if not compact and sig.notes:
        skip = set((sig.session or {}).get("reasons") or []) | {"Technicals and news agree"}
        notes = [n for n in sig.notes if n not in skip]
        if notes:
            parts.append("\n".join(f"• {escape(n)}" for n in notes))
    return "\n\n".join(parts)


def price_action_line(tech: TechSnapshot) -> str:
    """One line: H4 / H1 structure and the regime."""
    pa = tech.pa
    if pa is None:
        return ""
    def st(x, tf):
        return f"{_STRUCT_ICON.get(x.trend, '⚪')} {tf} {x.trend}" + (f" ({escape(x.label)})" if x.label else "")
    adx = f" · ADX {pa.adx:.0f}" if pa.adx == pa.adx else ""
    return f"🧱 {st(pa.h4, 'H4')} · {st(pa.h1, 'H1')} · {escape(pa.regime)}{adx}"


def format_signal_message(tech: TechSnapshot, sig: Signal, source: str, as_of: datetime) -> str:
    pa = price_action_line(tech)
    return f"{price_header(tech)}\n\n{format_signal(sig)}" + (f"\n\n{pa}" if pa else "") + f"\n\n{_footer(source, as_of)}"


# ---------------------------------------------------------------------------
# price action
# ---------------------------------------------------------------------------
def confluence_line(items) -> str:
    """'Zone confluence: EMA50 · 61.8% fib · prev-day low (3)' – or a plain warning when there is none."""
    items = [c for c in (items.split(" · ") if isinstance(items, str) else list(items or [])) if c]
    if not items:
        return "Zone confluence: <i>none — the zone is only the moving-average pullback</i>"
    strength = "strong" if len(items) >= 3 else "fair" if len(items) == 2 else "thin"
    return f"Zone confluence: <b>{escape(' · '.join(items))}</b> ({strength})"


_STRUCT_ICON = {"bullish": "🟢", "bearish": "🔴", "range": "⚪"}
_LEVEL_SHORT = {"PDH": "Prev-day high", "PDL": "Prev-day low", "PWH": "Prev-week high", "PWL": "Prev-week low",
                "ASIA_H": "Asia high", "ASIA_L": "Asia low", "VWAP": "Daily VWAP", "Round": "Round number"}


def format_price_action(tech: TechSnapshot) -> str:
    """Structure, regime, imbalances and key levels – the chart read in words and one table."""
    pa = tech.pa
    if pa is None:
        return ""
    h1, h4 = pa.h1, pa.h4

    def struct_text(st, tf: str) -> str:
        icon = _STRUCT_ICON.get(st.trend, "⚪")
        txt = f"{icon} <b>{tf}</b> {escape(st.trend)}" + (f" ({escape(st.label)})" if st.label else "")
        if st.bos:
            txt += f" · BOS {st.bos} at {st.bos_level:,.1f}"
        if st.choch:
            txt += f" · CHoCH {st.choch} at {st.choch_level:,.1f}"
        return txt

    lines = [struct_text(h4, "H4"), struct_text(h1, "H1")]
    regime = pa.regime
    adx_txt = f"ADX {pa.adx:.0f}" if pa.adx == pa.adx else "ADX n/a"
    vol = ("high" if pa.atr_pct >= 85 else "low" if pa.atr_pct <= 15 else "normal")
    lines.append(f"🌡 Regime: <b>{escape(regime)}</b> ({adx_txt}) · volatility {vol} (ATR {pa.atr_pct:.0f}th pct)")
    if pa.divergence:
        lines.append(f"⚠️ {pa.divergence.title()} RSI divergence at the last swing {'high' if pa.divergence == 'bearish' else 'low'}")
    if pa.candle:
        lines.append(f"🕯 {escape(pa.candle[0].upper() + pa.candle[1:])}")
    if pa.impulse:
        imp = pa.impulse
        lv = imp["levels"]
        lines.append(f"📏 Last impulse {imp['direction']} {imp['from']:,.1f} → {imp['to']:,.1f} · fib 50% {lv['50']:,.1f} · 61.8% {lv['61.8']:,.1f}")
    # key levels table
    below, above = tech.levels_around(max_dist_atr=4.0)
    rows: list[tuple] = []
    for name, lvl in reversed(above[:4]):
        rows.append((_LEVEL_SHORT.get(name, name), f"{lvl:,.1f}", _diff(lvl - tech.price)))
    rows.append(("▶ now", f"{tech.price:,.1f}", ""))
    for name, lvl in below[:4]:
        rows.append((_LEVEL_SHORT.get(name, name), f"{lvl:,.1f}", _diff(lvl - tech.price)))
    zones = []
    for ob in pa.order_blocks[-2:]:
        zones.append(f"{ob['kind']} order block {ob['lo']:,.1f}–{ob['hi']:,.1f}")
    for g in pa.fvgs[-2:]:
        zones.append(f"{g['kind']} fair value gap {g['lo']:,.1f}–{g['hi']:,.1f}")
    out = "🧱 <b>PRICE ACTION</b>\n" + "\n".join(lines)
    if len(rows) > 1:
        out += "\n<b>Key levels</b>\n" + _pre(rows, right=(1, 2))
    if zones:
        out += "\n<b>Unfilled zones</b>\n" + "\n".join(f"• {escape(z)}" for z in zones)
    return out


# ---------------------------------------------------------------------------
# technicals / levels
# ---------------------------------------------------------------------------
def format_technicals(tech: TechSnapshot) -> str:
    rows = [
        ("EMA 20 (H1)", f"{tech.ema20:,.1f}"),
        ("EMA 50 (H1)", f"{tech.ema50:,.1f}"),
        ("EMA 200 (H1)", f"{tech.ema200:,.1f}"),
        ("EMA 50 (D1)", f"{tech.daily_ema50:,.1f}"),
        ("EMA 200 (D1)", f"{tech.daily_ema200:,.1f}"),
        ("RSI (14)", f"{tech.rsi:.1f}"),
        ("MACD hist", _diff(tech.macd_hist, 2)),
        ("Bollinger %B", f"{tech.bb_pct:.2f}"),
    ]
    out = f"📈 <b>TECHNICALS</b> — score <b>{_m(f'{tech.score:+d}')}</b> · {escape(tech.trend_label)}\n{_pre(rows)}"
    if tech.pivots:
        out += "\n<b>Daily pivots</b>\n" + _pivot_table(tech.pivots)
    notes = [f"• {escape(r)}" for r in tech.reasons] + [f"⚠️ {escape(f)}" for f in tech.flags]
    return out + ("\n" + "\n".join(notes) if notes else "")


def _pivot_table(p: dict) -> str:
    return _pre([("R2", f"{p['R2']:,.1f}"), ("R1", f"{p['R1']:,.1f}"), ("Pivot", f"{p['P']:,.1f}"),
                 ("S1", f"{p['S1']:,.1f}"), ("S2", f"{p['S2']:,.1f}")])


def format_levels(tech: TechSnapshot) -> str:
    res = sorted(r for r in tech.resistances if r > tech.price)[:4]
    sup = sorted((s for s in tech.supports if s < tech.price), reverse=True)[:4]
    rows: list[tuple] = [("Resistance", "", "")]
    rows += [("", f"{r:,.1f}", _diff(r - tech.price)) for r in reversed(res)] or [("", "none nearby", "")]
    rows.append(("▶ now", f"{tech.price:,.1f}", ""))
    rows.append(("Support", "", ""))
    rows += [("", f"{s:,.1f}", _diff(s - tech.price)) for s in sup] or [("", "none nearby", "")]
    out = f"📐 <b>KEY LEVELS</b> — price <b>{tech.price:,.2f}</b> · ATR {tech.atr:,.1f}\n{_pre(rows)}"
    if tech.pivots:
        out += "\n<b>Daily pivots</b>\n" + _pivot_table(tech.pivots)
    out += "\n<b>EMA (H1)</b>\n" + _pre([("EMA 20", f"{tech.ema20:,.1f}"), ("EMA 50", f"{tech.ema50:,.1f}"), ("EMA 200", f"{tech.ema200:,.1f}")])
    pa = tech.pa
    if pa is not None:
        kl = [(k, v) for k, v in (("Prev-day high", pa.levels.get("PDH")), ("Prev-day low", pa.levels.get("PDL")),
                                  ("Prev-week high", pa.levels.get("PWH")), ("Prev-week low", pa.levels.get("PWL")),
                                  ("Asia high", pa.levels.get("ASIA_H")), ("Asia low", pa.levels.get("ASIA_L")),
                                  ("Daily VWAP", pa.levels.get("VWAP"))) if v]
        if kl:
            out += "\n<b>Session levels</b>\n" + _pre([(k, f"{v:,.1f}", _diff(v - tech.price)) for k, v in kl], right=(1, 2))
        if pa.impulse:
            lv = pa.impulse["levels"]
            out += (f"\n<b>Fib of the last {pa.impulse['direction']} leg</b> {pa.impulse['from']:,.1f} → {pa.impulse['to']:,.1f}\n"
                    + _pre([(f"{k}%", f"{v:,.1f}", _diff(v - tech.price)) for k, v in lv.items()], right=(1, 2)))
    return out


# ---------------------------------------------------------------------------
# news
# ---------------------------------------------------------------------------
def format_news(news: NewsScore, max_items: int = 8, hours: int = 36) -> str:
    head = (f"📰 <b>NEWS FLOW</b> — score <b>{_m(f'{news.score:+d}')}</b> · {escape(news.label)}\n"
            f"🟢 {news.n_bullish} bullish · 🔴 {news.n_bearish} bearish · ⚪ {news.n_neutral} neutral <i>(last {hours}h)</i>")
    if news.top_themes:
        head += "\nThemes: " + " · ".join(f"{escape(t)} <b>{_m(f'{v:+.1f}')}</b>" for t, v in news.top_themes)
    if not news.drivers:
        return head + "\n\n<i>No relevant headlines yet – the first scan may still be running.</i>"
    return head + "\n\n" + "\n".join(headline_line(a) for a in news.drivers[:max_items])


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


def _event_line(e) -> str:
    flame = "🔥" if e.impact == "High" else "🔸"
    line = f"{flame} <b>{escape(e.title)}</b>\n     {e.when.strftime('%a %H:%M UTC')} · {_countdown(e.minutes_away)}"
    bits = []
    if e.forecast:
        bits.append(f"forecast {escape(e.forecast)}")
    if e.previous:
        bits.append(f"previous {escape(e.previous)}")
    if e.actual:
        bits.append(f"actual <b>{escape(e.actual)}</b>")
    if bits:
        line += "\n     <i>" + " · ".join(bits) + "</i>"
    return line


def format_calendar(events: list, limit: int = 8) -> str:
    head = "📅 <b>UPCOMING USD RELEASES</b>\n<i>🔥 high impact · 🔸 medium impact</i>"
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
    return (f"⏰ <b>{escape(e.title)}</b> {_countdown(e.minutes_away)} ({e.when.strftime('%H:%M UTC')})\n"
            f"High-impact USD release. Gold often moves sharply and spreads widen — "
            f"consider tightening risk or waiting for the print.{extra}")


# ---------------------------------------------------------------------------
# trade signals
# ---------------------------------------------------------------------------
STATUS_LABEL = {
    "pending": "SETUP", "active": "LIVE", "tp1": "TP 1 HIT · stop at break-even", "tp2": "TP 2 HIT ✅",
    "stopped": "STOPPED OUT ❌", "flipped": "CLOSED · bias flipped", "expired": "CLOSED · max hold",
    "missed": "MISSED · no trade", "invalidated": "INVALIDATED · no trade", "cancelled": "CANCELLED · no trade",
    "stale": "EXPIRED · no trade",
}


def format_trade_signal(t, price: float | None = None, sizing: Sizing | None = None) -> str:
    side = "🟢 BUY" if t.side == "BUY" else "🔴 SELL"
    sgn = t.sign
    title = f"<b>{side} XAU/USD — {STATUS_LABEL.get(t.status, t.status.upper())}</b>"

    if t.status == "pending":
        mid = t.entry
        rows = [
            ("Stop", f"{t.sl:,.1f}", _diff(t.sl - mid)),
            ("TP 1", f"{t.tp1:,.1f}", f"{_diff(t.tp1 - mid)}  {abs(t.tp1 - mid) / t.risk:.1f}R"),
            ("TP 2", f"{t.tp2:,.1f}", f"{_diff(t.tp2 - mid)}  {t.rr:.1f}R"),
            ("No chase", _num(t.chase), ""),
            ("R:R", f"1 : {t.rr:.1f}", ""),
            ("Confidence", f"{t.confidence}%", ""),
        ]
        sr = _sizing_row(sizing)
        if sr:
            rows.append(sr)
        if price is not None:
            rows.append(("Now", f"{price:,.1f}", f"{_diff(price - mid)} vs mid"))
        waiting = ("Price is in the zone — waiting for a 15-minute confirmation."
                   if (t.setup_mode == "in-zone" or t.touched) else "Waiting for a pullback into the zone.")
        note = sizing_note(sizing, mid, t.tp1, t.tp2)
        return (f"{title}\nEntry zone <b>{_num(t.zone_lo)} – {_num(t.zone_hi)}</b>\n{_pre(rows)}\n"
                + (note + "\n" if note else "") +
                f"{waiting} A live call is posted the moment it confirms.\n"
                f"<i>Setup posted {t.opened_at.strftime('%d %b %H:%M UTC')}</i>")

    rows = [
        ("Entry", f"{t.entry:,.1f}", ""),
        ("Stop", f"{t.effective_sl:,.1f}", f"{_diff(t.effective_sl - t.entry)}  {_r((t.effective_sl - t.entry) * sgn / t.risk, 1)}"),
        ("TP 1", f"{t.tp1:,.1f}", f"{_diff(t.tp1 - t.entry)}  {abs(t.tp1 - t.entry) / t.risk:.1f}R"),
        ("TP 2", f"{t.tp2:,.1f}", f"{_diff(t.tp2 - t.entry)}  {t.rr:.1f}R"),
        ("R:R", f"1 : {t.rr:.1f}", ""),
        ("Confidence", f"{t.confidence}%", ""),
    ]
    if t.is_open:
        sr = _sizing_row(sizing)
        if sr:
            rows.append(sr)
        if price is not None:
            pnl = (price - t.entry) * sgn
            rows.append(("Now", f"{price:,.1f}", f"{_diff(pnl)}  {_r(t.r_at(price))}"))
    elif t.result_r is not None:
        mark = "✅" if t.result_r > 0.05 else ("❌" if t.result_r < -0.05 else "➖")
        rows.append(("Result", f"{t.exit_price:,.1f}", f"{_r(t.result_r)} {mark}"))
    when = t.triggered_at or t.opened_at
    tail = []
    if t.is_open:
        note = sizing_note(sizing, t.entry, t.tp1, t.tp2)
        if note:
            tail.append(note)
    if t.trigger_note:
        tail.append(f"Confirmed by: <b>{escape(t.trigger_note)}</b>")
    if getattr(t, "confluence", ""):
        tail.append(confluence_line(t.confluence))
    if t.reason:
        tail.append(f"<i>{escape(t.reason)}</i>")
    tail.append(f"<i>{'Entered' if t.traded else 'Posted'} {when.strftime('%d %b %H:%M UTC')}</i>")
    return f"{title}\n{_pre(rows)}\n" + "\n".join(tail)


def format_signals_overview(active, history: list, stats: dict, price: float | None, sizing_for=None, session: dict | None = None,
                            mine: dict | None = None) -> str:
    parts = ["📡 <b>TRADE SIGNALS</b>"]
    if active:
        sz = sizing_for(active.entry, active.sl) if sizing_for else None
        parts.append(format_trade_signal(active, price, sz))
    else:
        parts.append("⚪ <b>No call right now.</b>\nA setup is posted when technicals and news line up with enough confidence; "
                     "it goes live after a 15-minute confirmation in the entry zone.")
    sl = session_line(session)
    if sl:
        parts.append(sl)
    closed = [h for h in history if h.traded and not h.is_open][:8]
    if closed:
        rows = []
        for h in closed:
            res = _r(h.result_r) if h.result_r is not None else "–"
            mark = "✅" if (h.result_r or 0) > 0.05 else ("❌" if (h.result_r or 0) < -0.05 else "➖")
            rows.append((h.opened_at.strftime("%d %b"), h.side, f"{h.entry:,.0f} → {h.exit_price:,.0f}", f"{res} {mark}"))
        parts.append("📜 <b>RECENT CALLS</b>\n" + _pre(rows, right=(3,)))
    if stats.get("count"):
        extra = f" · setups skipped {stats['no_trade']}" if stats.get("no_trade") else ""
        parts.append(f"📊 Closed <b>{stats['count']}</b> · win rate <b>{stats['win_rate']}%</b> · "
                     f"total <b>{_r(stats['total_r'])}</b> · average <b>{_r(stats['avg_r'])}</b>{extra}")
    if mine and mine.get("count"):
        parts.append(f"🧾 Calls you took: <b>{mine['count']}</b> · win rate <b>{mine['win_rate']}%</b> · total <b>{_r(mine['total_r'])}</b>"
                     f" — mark calls with /took or /skipped")
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def format_mark(sig, mark: str) -> str:
    verb = "Marked as taken" if mark == "took" else "Marked as skipped"
    when = (sig.triggered_at or sig.opened_at).strftime("%d %b %H:%M UTC")
    return (f"✅ <b>{verb}:</b> #{sig.id} {sig.side} @ {sig.entry:,.1f} ({when}) · {escape(STATUS_LABEL.get(sig.status, sig.status))}\n"
            f"<i>/signals shows the results of the calls you took separately.</i>")


def format_scorecard(week: dict, allt: dict, mine: dict | None, selfcheck: dict | None, best_rows: list, period_label: str) -> str:
    """Weekly scorecard: the week's calls, the running record, your own taken calls, and the self-check line."""
    parts = [f"🗓 <b>WEEKLY SCORECARD</b> — {escape(period_label)}"]
    wr = f"{week['win_rate']:.0f}%" if week.get("win_rate") is not None else "–"
    rows = [
        ("Setups posted", str(week.get("setups", 0))),
        ("Trades closed", f"{week.get('count', 0)} ({week.get('wins', 0)} W · {week.get('losses', 0)} L · {week.get('breakeven', 0)} BE)"),
        ("Win rate", wr),
        ("Total / average", f"{_r(week.get('total_r', 0.0), 1)} / {_r(week['avg_r']) if week.get('avg_r') is not None else '–'}"),
        ("Best / worst", f"{_r(week['best_r']) if week.get('best_r') is not None else '–'} / {_r(week['worst_r']) if week.get('worst_r') is not None else '–'}"),
        ("No-trade setups", str(week.get("no_trade", 0))),
    ]
    parts.append(_pre(rows, right=()))
    if best_rows:
        parts.append("<b>This week's calls</b>\n" + _pre(best_rows, right=(3,)))
    if allt.get("count"):
        parts.append(f"📈 Running record: <b>{allt['count']}</b> trades · win rate <b>{allt['win_rate']}%</b> · total <b>{_r(allt['total_r'], 1)}</b> · average <b>{_r(allt['avg_r'])}</b>")
    if mine and mine.get("count"):
        parts.append(f"🧾 Calls you took: <b>{mine['count']}</b> · win rate <b>{mine['win_rate']}%</b> · total <b>{_r(mine['total_r'], 1)}</b>")
    if selfcheck and selfcheck.get("count"):
        parts.append(f"🧪 Self-check (last {selfcheck.get('days', 60):.0f} days of real bars, technical-only): {selfcheck['count']} trades · "
                     f"win rate {selfcheck['win_rate']}% · average {_r(selfcheck['avg_r'])} · total {_r(selfcheck['total_r'], 1)}")
    parts.append("<i>Results count half booked at TP1 and are net of spread and stop slippage.</i>")
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def format_signal_event(e, sizing: Sizing | None = None) -> str:
    s = e.signal
    head = {
        "setup": f"📐 <b>NEW {s.side} SETUP</b> — wait for the zone",
        "triggered": f"📡 <b>{s.side} CONFIRMED — LIVE CALL</b>",
        "tp1": "🎯 <b>TAKE PROFIT 1 HIT</b>",
        "tp2": "🏁 <b>TAKE PROFIT 2 HIT — closed</b>",
        "stopped": "🛑 <b>STOPPED OUT — closed</b>",
        "flipped": "🔁 <b>CLOSED — bias flipped</b>",
        "expired": "⌛ <b>CLOSED — max hold reached</b>",
        "missed": "🏃 <b>SETUP MISSED</b> — price ran without a pullback, no trade",
        "invalidated": "🚫 <b>SETUP INVALIDATED</b> — no trade",
        "cancelled": "↩️ <b>SETUP CANCELLED</b> — bias changed, no trade",
        "stale": "⌛ <b>SETUP EXPIRED</b> — no confirmation in time, no trade",
    }.get(e.kind, e.kind)
    body = format_trade_signal(s, e.price if s.is_live else None, sizing)
    return f"{head}\n\n{body}\n\n{DISCLAIMER}"


# ---------------------------------------------------------------------------
# full report
# ---------------------------------------------------------------------------
def format_full_report(tech: TechSnapshot, news: NewsScore, sig: Signal, briefing: str | None,
                       source: str, as_of: datetime, max_headlines: int = 6, hours: int = 36,
                       events: list | None = None, trade=None, sizing: Sizing | None = None) -> str:
    sections = [price_header(tech), format_signal(sig)]
    if trade is not None:
        sections.append(format_trade_signal(trade, tech.price, sizing))
    nxt = next((e for e in (events or []) if e.impact == "High" and e.minutes_away > -15), None)
    if nxt is not None:
        sections.append(format_next_event(nxt))
    if briefing:
        sections.append(f"🧠 <b>AI BRIEFING</b>\n{escape(briefing)}")
    pa_text = format_price_action(tech)
    if pa_text:
        sections.append(pa_text)
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
    return (f"🔔 <b>BIAS CHANGED</b> — {was} → {sig.emoji} <b>{escape(sig.label)}</b>\n"
            f"XAU/USD <b>{tech.price:,.2f}</b>\n\n{format_signal(sig, compact=True)}\n\n{DISCLAIMER}")


def format_alert_news(articles: list[Article]) -> str:
    return "🚨 <b>HIGH-IMPACT GOLD NEWS</b>\n\n" + "\n".join(headline_line(a) for a in articles[:5])


def format_alert_price(tech: TechSnapshot, move_pct: float, window_label: str) -> str:
    direction = "jumped" if move_pct > 0 else "dropped"
    icon = "🚀" if move_pct > 0 else "📉"
    rows = [("Now", f"{tech.price:,.2f}"), ("RSI (14)", f"{tech.rsi:.0f}"), ("ATR (14)", f"{tech.atr:,.1f}"),
            ("Support", _num(tech.nearest_support())), ("Resistance", _num(tech.nearest_resistance()))]
    return f"{icon} <b>XAU/USD {direction} {abs(move_pct):.2f}% in the last {window_label}</b>\n{_pre(rows, right=())}"


# ---------------------------------------------------------------------------
# self-check (historical replay)
# ---------------------------------------------------------------------------
def _bt_block(name: str, b: dict) -> tuple:
    wr = f"{b['win_rate']:.0f}%" if b.get("win_rate") is not None else "–"
    avg = _r(b["avg_r"]) if b.get("avg_r") is not None else "–"
    return (name, str(b.get("count", 0)), wr, avg)


def format_backtest(bt: dict | None, on_start: bool = True, variants: dict | None = None) -> str:
    title = "🧪 <b>SELF-CHECK</b> — how these rules did on real bars"
    if not bt or not isinstance(bt, dict) or "summary" not in bt:
        why = ("It runs automatically a few minutes after the bot starts and takes about ten minutes; check back soon."
               if on_start else "It is switched off (BACKTEST_ON_START).")
        return f"{title}\n\nNo replay has been recorded yet. {why}\n\n{DISCLAIMER}"
    s, per = bt["summary"], bt.get("period") or {}
    try:
        f = datetime.fromisoformat(per["from"]).strftime("%d %b")
        t = datetime.fromisoformat(per["to"]).strftime("%d %b")
        when = f"{f} → {t} ({per.get('days', 0):.0f} days)"
    except (KeyError, ValueError, TypeError):
        when = "n/a"
    wr = f"{s['win_rate']:.1f}%" if s.get("win_rate") is not None else "–"
    pf = "–" if s.get("profit_factor") is None else f"{s['profit_factor']:.2f}"
    rows = [
        ("Period", when),
        ("Setups", f"{s.get('setups', 0)} · traded {s.get('count', 0)} · no trade {s.get('no_trade', 0)}"),
        ("Win rate", f"{wr}  ({s.get('wins', 0)} W · {s.get('losses', 0)} L · {s.get('breakeven', 0)} BE)"),
        ("Average", f"{_r(s['avg_r']) if s.get('avg_r') is not None else '–'} per trade · total {_r(s.get('total_r', 0.0), 1)}"),
        ("Profit factor", f"{pf} · max drawdown {s.get('max_drawdown_r', 0):.1f}R"),
        ("TP1 reached", f"{s['tp1_rate']:.0f}% of trades · avg hold {s.get('avg_hold_h') or 0:.1f} h" if s.get("tp1_rate") is not None else "–"),
    ]
    parts = [title, _pre(rows, right=())]
    for label, key in (("BY CONFIDENCE", "by_confidence"), ("BY SESSION", "by_session"), ("BY CONFIRMATION", "by_trigger")):
        grp = bt.get(key) or {}
        if grp:
            trows = [("", "Trades", "Win", "Avg")] + [_bt_block(k.replace("15m ", ""), v) for k, v in grp.items()]
            parts.append(f"<b>{label}</b>\n{_pre(trows, right=(1, 2, 3))}")
    th = bt.get("thresholds") or {}
    if th:
        trows = [("Min conf", "Trades", "Win", "Avg")] + [_bt_block(f"≥ {k}%", v) for k, v in th.items() if v.get("count")]
        if len(trows) > 1:
            parts.append(f"<b>IF THE BAR WERE HIGHER</b>\n{_pre(trows, right=(1, 2, 3))}")
    rows_v = (variants or {}).get("variants") or []
    if rows_v:
        trows = [("", "Trades", "Win", "Avg", "Total")]
        for v in rows_v:
            b = v.get("summary") or {}
            if not b.get("count"):
                continue
            wr = f"{b['win_rate']:.0f}%" if b.get("win_rate") is not None else "–"
            trows.append((v["name"], str(b["count"]), wr, _r(b["avg_r"]) if b.get("avg_r") is not None else "–",
                          _r(b["total_r"], 1) if b.get("total_r") is not None else "–"))
        if len(trows) > 1:
            parts.append(f"<b>SAME CALLS, OTHER MANAGEMENT</b>\n{_pre(trows, right=(1, 2, 3, 4))}")
    gen = bt.get("generated_at")
    try:
        gen_s = datetime.fromisoformat(gen).strftime("%d %b %H:%M UTC")
    except (TypeError, ValueError):
        gen_s = "n/a"
    pr = bt.get("params") or {}
    costs = (f"net of a ${pr['spread_usd']:.2f} spread and ${pr['slippage_usd']:.2f} slippage on stops"
             if pr.get("spread_usd") is not None else "before spread")
    parts.append(f"<i>Technical-only replay on {escape(str(bt.get('symbol', 'gold')))} bars — news and calendar blackouts are not included; "
                 f"entries at the confirming 15-minute close, stops and targets fill at their level, stop checked before targets inside a bar; "
                 f"results count half the position booked at TP1 and are {costs}. Past replay results never predict future results. Run {gen_s}.</i>")
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
HELP = """📖 <b>COMMANDS</b>

<b>Market</b>
/price — live price, trend, RSI, ATR, nearest levels
/levels — support, resistance, pivots, session levels and fibs
/news — latest gold-moving headlines with sentiment
/calendar — upcoming CPI, FOMC, payrolls and other USD releases

<b>Signals</b>
/signal — current bias, confidence and the entry plan
/signals — setup or live BUY / SELL call with stop, targets and results
/analyze — full report with chart, price action, technicals and news
/backtest — how these rules did on the last two months of real bars

<b>Settings</b>
/risk 5000 1 — account size and % risk per trade → lot size on every call
/took · /skipped — mark the latest call as taken or skipped (your own record in /signals and the weekly scorecard)
/subscribe · /unsubscribe — scheduled reports in this chat
/alerts on · /alerts off — bias changes, big moves, breaking news, pre-release warnings
/status — bot status
/help — this list

""" + DISCLAIMER
