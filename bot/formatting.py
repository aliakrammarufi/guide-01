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

import re

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
def _chg(x: float) -> str:
    """'▼ 0.40%' – the day change without a sign (the arrow carries it)."""
    arrow = "▲" if x > 0.005 else ("▼" if x < -0.005 else "•")
    return f"{arrow} {abs(x):.2f}%"


def _r_mult(level: float, ref: float, risk: float) -> str:
    return f"({abs(level - ref) / risk:.1f}R)" if risk and risk > 0 else ""


def levels_block(zone_lo=None, zone_hi=None, entry=None, sl=None, tp1=None, tp2=None, sizing: Sizing | None = None,
                 now: float | None = None, r_now: float | None = None, ref: float | None = None) -> str:
    """The stacked levels of the update: ENTRY ZONE / ENTRY, SL, TP1, TP2, Size – aligned in a monospace block."""
    ref = ref if ref is not None else (entry if entry is not None else ((zone_lo + zone_hi) / 2 if zone_lo is not None else None))
    risk = abs(ref - sl) if (ref is not None and sl is not None) else 0.0
    rows: list[tuple] = []
    if entry is not None:
        extra = f"   now {now:,.1f} · {_r(r_now)}" if (now is not None and r_now is not None and abs(now - entry) >= 0.05) else ""
        rows.append(("ENTRY", f"{entry:,.1f}{extra}"))
    elif zone_lo is not None:
        rows.append(("ENTRY ZONE", f"{zone_lo:,.1f} – {zone_hi:,.1f}"))
    if sl is not None:
        rows.append(("SL", f"{sl:,.1f}" + (f"   ({risk:,.1f} pts)" if risk else "")))
    if tp1 is not None:
        rows.append(("TP1", f"{tp1:,.1f}   {_r_mult(tp1, ref, risk)}".rstrip()))
    if tp2 is not None:
        rows.append(("TP2", f"{tp2:,.1f}   {_r_mult(tp2, ref, risk)}".rstrip()))
    if sizing is not None:
        rows.append(("Size", f"{sizing.lots:.2f} lots · risk {sizing.money(sizing.actual_risk)} ({sizing.pct_of_account:.1f}%)"))
    return _pre(rows, right=()) if rows else ""


def bias_lines(sig: Signal, tech: TechSnapshot | None = None, tag: str = "", trade=None) -> str:
    """'BIAS: SELL 🔴 52%' and the trend line. With a setup or live call, its side and confidence lead."""
    if trade is not None:
        label, emoji, conf = trade.side, ("🔴" if trade.side == "SELL" else "🟢"), trade.confidence
    else:
        label, emoji, conf = BIAS_WORD.get(sig.bias, sig.bias), sig.emoji, sig.confidence
    head = f"<b>BIAS: {escape(label)} {emoji} {conf}%</b>" + (f" · {tag}" if tag else "")
    pa = tech.pa if tech is not None else None
    if pa is not None:
        adx = f" · ADX {pa.adx:.0f}" if pa.adx == pa.adx else ""
        trend = f"Trend: H4 {escape(pa.h4.trend)} · H1 {escape(pa.h1.trend)}{adx}"
    else:
        trend = f"Trend: {escape(tech.trend_label)}" if tech is not None else "Trend: n/a"
    return head + "\n" + trend


def no_call_lines(sig: Signal) -> str:
    """Why there is no call right now, in one line, plus the levels that matter meanwhile."""
    if sig.direction == "flat":
        why = "bias is neutral"
    else:
        why = f"confidence {sig.confidence}% is below the {MIN_CONF}% needed"
    reasons = []
    if sig.agreement == "conflict":
        reasons.append("technicals and news disagree")
    sess = sig.session or {}
    if sess.get("label") and (not sess.get("allow_entries", True) or (sess.get("conf_adj") or 0) < 0):
        reasons.append(escape(sess["label"]))
    line = f"No call — {why}" + (f" ({' · '.join(reasons)})" if reasons else "") + ". Waiting."
    return f"{line}\nLevels: R <b>{_num(sig.nearest_resistance)}</b> · S <b>{_num(sig.nearest_support)}</b>"


def wait_line(sig_or_trade, chase=None, in_zone: bool = False) -> str:
    if in_zone:
        return "⚠️ Price is in the zone — wait for a 15-min confirmation."
    return "⚠️ Wait for a 15-min confirmation in the zone." + (f" No chase beyond {chase:,.1f}." if chase else "")


def plan_line(sig: Signal) -> str:
    """Stop · TP1 · TP2 · R:R on one line (the levels you act on)."""
    if sig.direction == "flat" or sig.stop is None or sig.zone_lo is None:
        return ""
    return (f"Stop <b>{_num(sig.stop)}</b> · TP1 <b>{_num(sig.target1)}</b> · TP2 <b>{_num(sig.target2)}</b>"
            f" · R:R 1:{sig.planned_rr or 0:.1f}")


def entry_hint(sig: Signal) -> str:
    if sig.direction == "flat" or sig.zone_lo is None:
        return ""
    if sig.entry_mode == "in-zone":
        return "Price is in the zone — wait for a 15-minute confirmation."
    return f"Wait for a pullback into the zone and a 15-minute confirmation. Don't chase beyond {_num(sig.chase_limit)}."


def format_signal(sig: Signal, compact: bool = False) -> str:
    """Bias, confidence, the plan. Short: the decision and the levels, nothing to decode."""
    agree = {"aligned": "aligned", "conflict": "conflict ⚠️", "one-sided": "one-sided"}.get(sig.agreement, sig.agreement)
    lines = [f"{sig.emoji} <b>{escape(sig.label.upper())}</b> — confidence <b>{sig.confidence}%</b> ({_confidence_word(sig.confidence)})",
             f"Technicals {_m(f'{sig.tech_score:+d}')} · News {_m(f'{sig.news_score:+d}')} · {agree}"]
    if sig.direction != "flat" and sig.stop is not None and sig.zone_lo is not None:
        side = "BUY" if sig.direction == "long" else "SELL"
        lines.append(f"🎯 <b>{side} PLAN</b> — zone <b>{_num(sig.zone_lo)} – {_num(sig.zone_hi)}</b>")
        lines.append(plan_line(sig))
        lines.append(entry_hint(sig))
    else:
        lines.append("No trade plan — bias is not strong enough for a call.")
    sl = session_line(sig.session)
    if sl:
        lines.append(sl)
    if not compact and sig.notes:
        skip = set((sig.session or {}).get("reasons") or []) | {"Technicals and news agree"}
        notes = [n for n in sig.notes if n not in skip][:3]
        if notes:
            lines.append("\n".join(f"• {escape(n)}" for n in notes))
    return "\n".join(lines)


def price_action_line(tech: TechSnapshot) -> str:
    """One line: H4 / H1 structure and the regime."""
    pa = tech.pa
    if pa is None:
        return ""
    def st(x, tf):
        return f"{_STRUCT_ICON.get(x.trend, '⚪')} {tf} {x.trend}" + (f" ({escape(x.label)})" if x.label else "")
    adx = f" · ADX {pa.adx:.0f}" if pa.adx == pa.adx else ""
    m = getattr(tech, "macro", None)
    macro = f" · macro {escape(m.get('label', ''))}" if m else ""
    return f"🧱 {st(pa.h4, 'H4')} · {st(pa.h1, 'H1')} · {escape(pa.regime)}{adx}{macro}"


def format_signal_message(tech: TechSnapshot, sig: Signal, source: str, as_of: datetime, trade=None, sizing: Sizing | None = None,
                          events: list | None = None) -> str:
    return format_pulse(tech, sig, trade, sizing, events, as_of, title="XAU/USD SIGNAL")


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
    for name, lvl in reversed(above[:3]):
        rows.append((_LEVEL_SHORT.get(name, name), f"{lvl:,.1f}", _diff(lvl - tech.price)))
    rows.append(("▶ now", f"{tech.price:,.1f}", ""))
    for name, lvl in below[:3]:
        rows.append((_LEVEL_SHORT.get(name, name), f"{lvl:,.1f}", _diff(lvl - tech.price)))
    zones = []
    for ob in pa.order_blocks[-2:]:
        zones.append(f"{ob['kind']} order block {ob['lo']:,.1f}–{ob['hi']:,.1f}")
    for g in pa.fvgs[-2:]:
        zones.append(f"{g['kind']} fair value gap {g['lo']:,.1f}–{g['hi']:,.1f}")
    out = "🧱 <b>PRICE ACTION</b>\n" + "\n".join(lines)
    if len(rows) > 1:
        out += "\n<b>Key levels</b>\n" + _pre(rows, right=(1, 2))
    m = getattr(tech, "macro", None)
    if m and isinstance(m, dict):
        out += f"\n🌍 Macro {escape(m.get('label', 'mixed'))} for gold ({_m(f'{m.get('score', 0):+d}')})"
        for f in m.get("flags") or []:
            out += f" · ⚠️ {escape(f)}"
    return out


def macro_text(tech: TechSnapshot) -> str:
    m = getattr(tech, "macro", None)
    if not m or not isinstance(m, dict):
        return ""
    icon = "🟢" if m.get("score", 0) >= 4 else "🔴" if m.get("score", 0) <= -4 else "⚪"
    rows = []
    for key, label in (("dxy", "Dollar index"), ("us10y", "US 10y yield"), ("spx", "S&P 500")):
        pt = (m.get("parts") or {}).get(key)
        if not pt:
            continue
        arrow = "▲" if pt["trend"] == "up" else "▼"
        tone = "+" if pt["score"] > 0 else "−" if pt["score"] < 0 else "·"
        rows.append((label, f"{pt['last']:,.2f}", f"{arrow} {_m(f'{pt['chg5_pct']:+.1f}')}% 5d", f"{tone}gold"))
    out = f"🌍 <b>MACRO</b> — {icon} {escape(m.get('label', 'mixed'))} for gold ({_m(f'{m.get('score', 0):+d}')})"
    if rows:
        out += "\n" + _pre(rows, right=(1, 2))
    for f in m.get("flags") or []:
        out += f"\n⚠️ {escape(f)}"
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
    res = sorted(r for r in tech.resistances if r > tech.price)[:3]
    sup = sorted((s for s in tech.supports if s < tech.price), reverse=True)[:3]
    rows: list[tuple] = [("Resistance", "", "")]
    rows += [("", f"{r:,.1f}", _diff(r - tech.price)) for r in reversed(res)] or [("", "none nearby", "")]
    rows.append(("▶ now", f"{tech.price:,.1f}", ""))
    rows.append(("Support", "", ""))
    rows += [("", f"{s:,.1f}", _diff(s - tech.price)) for s in sup] or [("", "none nearby", "")]
    out = f"📐 <b>KEY LEVELS</b> — price <b>{tech.price:,.2f}</b> · ATR {tech.atr:,.1f}\n{_pre(rows)}"
    if tech.pivots:
        out += "\n<b>Daily pivots</b>\n" + _pivot_table(tech.pivots)
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
    return (f"⏰ <b>{escape(e.title)}</b> {_countdown(e.minutes_away)} ({e.when.strftime('%H:%M UTC')}) — high impact\n"
            f"Gold often moves sharply and spreads widen: tighten risk or wait for the print.{extra}")


# ---------------------------------------------------------------------------
# trade signals
# ---------------------------------------------------------------------------
STATUS_LABEL = {
    "pending": "SETUP", "active": "LIVE", "tp1": "TP 1 HIT · half booked", "tp2": "TP 2 HIT ✅",
    "stopped": "STOPPED OUT ❌", "flipped": "CLOSED · bias flipped", "expired": "CLOSED · max hold",
    "missed": "MISSED · no trade", "invalidated": "INVALIDATED · no trade", "cancelled": "CANCELLED · no trade",
    "stale": "EXPIRED · no trade",
}
BREAKEVEN_AFTER_TP1 = True     # mirrors SIGNAL_BREAKEVEN_AFTER_TP1 (set by state.py) – only changes the wording here
MIN_CONF = 45                  # mirrors SIGNAL_MIN_CONF (set by state.py): below this no setup is posted
BIAS_WORD = {"STRONG_BUY": "STRONG BUY", "BUY": "BUY", "NEUTRAL": "NEUTRAL", "SELL": "SELL", "STRONG_SELL": "STRONG SELL"}


def status_label(t) -> str:
    if t.status == "tp1":
        return "TP 1 HIT · stop at break-even" if t.sl_current is not None else "TP 1 HIT · half booked"
    return STATUS_LABEL.get(t.status, t.status.upper())


def management_line(t) -> str:
    """What to do with the position from here, matching how the tracker manages it."""
    if t.status == "active":
        after = "move the stop to break-even" if BREAKEVEN_AFTER_TP1 else f"keep the stop at {t.sl:,.1f}"
        return f"Plan: book half at TP 1, {after}, let the rest run to TP 2."
    if t.status == "tp1":
        stop = "at break-even" if t.sl_current is not None else f"stays at {t.sl:,.1f}"
        return f"Half booked at TP 1 · stop {stop} · the rest runs to TP 2."
    return ""


def _size_line(sz: Sizing | None, entry: float, tp1: float, tp2: float) -> str:
    if sz is None:
        return ""
    line = (f"{sz.lots:.2f} lots · risk <b>{sz.money(sz.actual_risk)}</b> ({sz.pct_of_account:.1f}%) · "
            f"TP1 ≈ +{sz.money(sz.profit_at(tp1 - entry))} · TP2 ≈ +{sz.money(sz.profit_at(tp2 - entry))}")
    if sz.over_guide and sz.is_fixed and sz.lots_by_risk < sz.lots:
        line += f"\n⚠️ Wide stop: {sz.lots:.2f} lots risks {sz.pct_of_account:.1f}% — {sz.lots_by_risk:.2f} lots keeps it at {sz.risk_pct:g}%."
    elif sz.over_guide:
        line += f"\n⚠️ Even 0.01 lot risks {sz.pct_of_account:.1f}% on this stop — consider skipping it."
    return line


def format_trade_signal(t, price: float | None = None, sizing: Sizing | None = None, with_title: bool = True) -> str:
    """The call on a few lines: levels, size, what to do. No tables, no filler."""
    side = "🟢 BUY" if t.side == "BUY" else "🔴 SELL"
    sgn = t.sign
    lines = [f"<b>{side} XAU/USD — {status_label(t)}</b>"] if with_title else []
    if t.status == "pending":
        lines.append(f"Zone <b>{_num(t.zone_lo)} – {_num(t.zone_hi)}</b> · Stop <b>{t.sl:,.1f}</b> · TP1 <b>{t.tp1:,.1f}</b> · TP2 <b>{t.tp2:,.1f}</b>"
                     f" · R:R 1:{t.rr:.1f} · conf {t.confidence}%")
        sl = _size_line(sizing, t.entry, t.tp1, t.tp2)
        if sl:
            lines.append(sl)
        lines.append("Price is in the zone — waiting for a 15-minute confirmation." if (t.setup_mode == "in-zone" or t.touched)
                     else f"Waiting for a pullback into the zone. Don't chase beyond {_num(t.chase)}.")
        return "\n".join(lines)
    lines.append(f"Entry <b>{t.entry:,.1f}</b> · Stop <b>{t.effective_sl:,.1f}</b> ({abs(t.effective_sl - t.entry):,.1f} pts)"
                 f" · TP1 <b>{t.tp1:,.1f}</b> · TP2 <b>{t.tp2:,.1f}</b> · R:R 1:{t.rr:.1f}")
    if t.is_open:
        sl = _size_line(sizing, t.entry, t.tp1, t.tp2)
        if sl:
            lines.append(sl)
        if price is not None and abs(price - t.entry) >= 0.05:
            lines.append(f"Now <b>{price:,.1f}</b> ({_diff((price - t.entry) * sgn)} · {_r(t.r_at(price))})")
        plan = management_line(t)
        if plan:
            lines.append(plan)
    elif t.result_r is not None:
        mark = "✅" if t.result_r > 0.05 else ("❌" if t.result_r < -0.05 else "➖")
        lines.append(f"Closed at <b>{t.exit_price:,.1f}</b> · <b>{_r(t.result_r)}</b> {mark}")
    if t.trigger_note:
        lines.append(f"Confirmed by {escape(t.trigger_note)} · conf {t.confidence}%")
    return "\n".join(lines)


def format_signals_overview(active, history: list, stats: dict, price: float | None, sizing_for=None, session: dict | None = None,
                            mine: dict | None = None) -> str:
    parts = ["📡 <b>TRADE SIGNALS</b>"]
    if active:
        sz = sizing_for(active.entry, active.sl) if sizing_for else None
        parts.append(format_trade_signal(active, price, sz))
    else:
        parts.append("⚪ <b>No call right now.</b> A setup is posted when the bias is strong enough; it goes live after a 15-minute confirmation.")
    sl = session_line(session)
    if sl:
        parts.append(sl)
    closed = [h for h in history if h.traded and not h.is_open][:5]
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
        parts.append(f"🧾 Calls you took: <b>{mine['count']}</b> · win rate <b>{mine['win_rate']}%</b> · total <b>{_r(mine['total_r'])}</b>")
    return "\n\n".join(parts)


def format_mark(sig, mark: str) -> str:
    verb = "Marked as taken" if mark == "took" else "Marked as skipped"
    when = (sig.triggered_at or sig.opened_at).strftime("%d %b %H:%M UTC")
    return (f"✅ <b>{verb}:</b> #{sig.id} {sig.side} @ {sig.entry:,.1f} ({when}) · {escape(status_label(sig))}\n"
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
    icon = "🔴" if s.side == "SELL" else "🟢"
    head_bias = f"<b>{s.side} {icon} {s.confidence}%</b>"
    if e.kind == "setup":
        return (f"📐 <b>NEW SETUP</b> · {head_bias}\n\n"
                f"{levels_block(zone_lo=s.zone_lo, zone_hi=s.zone_hi, sl=s.sl, tp1=s.tp1, tp2=s.tp2, sizing=sizing)}\n\n"
                f"{wait_line(s, s.chase, in_zone=(s.setup_mode == 'in-zone' or s.touched))}")
    if e.kind == "touched":
        return (f"👀 <b>ZONE REACHED</b> · {head_bias} at {e.price:,.1f}\n\n"
                f"{levels_block(zone_lo=s.zone_lo, zone_hi=s.zone_hi, sl=s.sl, tp1=s.tp1, tp2=s.tp2)}\n\n"
                f"⚠️ Watch the 15-min chart for a confirmation (EMA reclaim, engulfing, rejection wick, break of the pullback).")
    if e.kind == "triggered":
        return (f"📡 <b>LIVE CALL</b> · {head_bias}\n\n"
                f"{levels_block(entry=s.entry, sl=s.effective_sl, tp1=s.tp1, tp2=s.tp2, sizing=sizing)}\n\n"
                f"Confirmed by {escape(s.trigger_note or '15-min confirmation')}.\n{management_line(s)}")
    if e.kind == "tp1":
        stop = f"stop at break-even {s.effective_sl:,.1f}" if s.sl_current is not None else f"stop stays {s.sl:,.1f}"
        return (f"🎯 <b>TP1 HIT</b> · {s.side} from {s.entry:,.1f}\n"
                f"Half booked at <b>{e.price:,.1f}</b> ({_r(s.r_at(e.price))}) · {stop} · rest runs to TP2 <b>{s.tp2:,.1f}</b>")
    closes = {"tp2": "🏁 <b>TP2 HIT — closed</b>", "stopped": "🛑 <b>STOPPED OUT — closed</b>",
              "flipped": "🔁 <b>CLOSED — bias flipped</b>", "expired": "⌛ <b>CLOSED — max hold</b>"}
    if e.kind in closes:
        mark = "✅" if (s.result_r or 0) > 0.05 else ("❌" if (s.result_r or 0) < -0.05 else "➖")
        return f"{closes[e.kind]} · {s.side} from {s.entry:,.1f}\nClosed at <b>{e.price:,.1f}</b> · <b>{_r(s.result_r or 0.0)}</b> {mark}"
    no_trade = {"missed": "🏃 <b>SETUP MISSED</b> — price ran without a pullback, no trade",
                "invalidated": f"🚫 <b>SETUP INVALIDATED</b> — price went through {s.sl:,.1f} first, no trade",
                "cancelled": "↩️ <b>SETUP CANCELLED</b> — bias changed, no trade",
                "stale": "⌛ <b>SETUP EXPIRED</b> — no confirmation in time, no trade"}
    if e.kind in no_trade:
        return f"{no_trade[e.kind]}\n{s.side} zone was {_num(s.zone_lo)} – {_num(s.zone_hi)}"
    return f"{e.kind}: {escape(e.text)}"


# ---------------------------------------------------------------------------
# full report
# ---------------------------------------------------------------------------
def format_pulse(tech: TechSnapshot, sig: Signal, trade=None, sizing: Sizing | None = None, events: list | None = None,
                 as_of: datetime | None = None, news: NewsScore | None = None, briefing: str | None = None,
                 title: str = "XAU/USD UPDATE") -> str:
    """The scheduled update (and /signal): price · bias/trend · the levels · what to do · top headline · next release."""
    ts = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%H:%M UTC")
    parts = [f"📌 <b>{escape(title)}</b> · {ts}\nPrice <b>{tech.price:,.1f}</b> ({_chg(tech.change_24h_pct)})"]
    if trade is not None and trade.is_open:
        parts.append(bias_lines(sig, tech, tag="<b>LIVE CALL</b>", trade=trade))
        parts.append(levels_block(entry=trade.entry, sl=trade.effective_sl, tp1=trade.tp1, tp2=trade.tp2, sizing=sizing,
                                  now=tech.price, r_now=trade.r_at(tech.price), ref=trade.entry))
        parts.append(management_line(trade))
    elif trade is not None:
        parts.append(bias_lines(sig, tech, tag="setup", trade=trade))
        parts.append(levels_block(zone_lo=trade.zone_lo, zone_hi=trade.zone_hi, sl=trade.sl, tp1=trade.tp1, tp2=trade.tp2, sizing=sizing))
        parts.append(wait_line(trade, trade.chase, in_zone=(trade.setup_mode == "in-zone" or trade.touched)))
    elif sig.direction != "flat" and sig.stop is not None and sig.zone_lo is not None and sig.confidence >= MIN_CONF:
        parts.append(bias_lines(sig, tech))
        parts.append(levels_block(zone_lo=sig.zone_lo, zone_hi=sig.zone_hi, sl=sig.stop, tp1=sig.target1, tp2=sig.target2, sizing=sizing))
        parts.append(wait_line(sig, sig.chase_limit, in_zone=(sig.entry_mode == "in-zone")))
    else:
        parts.append(bias_lines(sig, tech))
        parts.append(no_call_lines(sig))
    tail = []
    if news is not None and news.drivers:
        top = news.drivers[0]
        tail.append(f"📰 {escape(top.title)} <i>({escape(top.source)}, {_age(top)})</i>")
    nxt = next((e for e in (events or []) if e.impact == "High" and e.minutes_away > -15), None)
    if nxt is not None:
        tail.append(f"⏱ {escape(nxt.title)} {_countdown(nxt.minutes_away)}")
    sl = session_line(sig.session)
    if sl and not (sig.session or {}).get("allow_entries", True):
        tail.append(sl)
    if tail:
        parts.append("\n".join(tail))
    if briefing:
        parts.append(f"🧠 <b>AI BRIEFING</b>\n{escape(briefing)}")
    return "\n\n".join(parts)


def _short_source(src: str, n: int = 18) -> str:
    src = (src or "").split(" – ")[0].split(" - ")[0].split(" | ")[0].strip()
    src = re.sub(r"^www\.", "", src)
    return src[:n].rstrip() + ("…" if len(src) > n else "")


def headline_short(a: Article, max_len: int = 95) -> str:
    """One line: icon, linked title (trimmed), short source, age."""
    title = a.title if len(a.title) <= max_len else a.title[:max_len].rsplit(" ", 1)[0] + "…"
    title = escape(title)
    if a.link:
        title = f'<a href="{escape(a.link, quote=True)}">{title}</a>'
    return f"{_impact_icon(a)} {title} <i>({_short_source(a.source)}, {_age(a)})</i>"


def format_structure(tech: TechSnapshot) -> str:
    """Structure in two lines + macro in one."""
    pa = tech.pa
    if pa is None:
        return ""
    def st(x, tf):
        txt = f"{_STRUCT_ICON.get(x.trend, '⚪')} {tf} {escape(x.trend)}" + (f" ({escape(x.label)})" if x.label else "")
        if x.bos:
            txt += f" · BOS {x.bos} {x.bos_level:,.1f}"
        if x.choch:
            txt += f" · CHoCH {x.choch} {x.choch_level:,.1f}"
        return txt
    adx = f"ADX {pa.adx:.0f}" if pa.adx == pa.adx else "ADX n/a"
    vol = "high" if pa.atr_pct >= 85 else "low" if pa.atr_pct <= 15 else "normal"
    lines = [st(pa.h4, "H4"), st(pa.h1, "H1"), f"{escape(pa.regime).capitalize()} · {adx} · volatility {vol}"
             + (f" · {escape(pa.divergence)} RSI divergence" if pa.divergence else "")]
    m = getattr(tech, "macro", None)
    if m and isinstance(m, dict):
        lines.append(f"🌍 Macro {escape(m.get('label', 'mixed'))} for gold ({_m(f'{m.get('score', 0):+d}')})")
    return "🧱 <b>STRUCTURE</b>\n" + "\n".join(lines)


def format_key_levels(tech: TechSnapshot) -> str:
    below, above = tech.levels_around(max_dist_atr=4.0)
    rows: list[tuple] = []
    for name, lvl in reversed(above[:3]):
        rows.append((_LEVEL_SHORT.get(name, name), f"{lvl:,.1f}", _diff(lvl - tech.price)))
    rows.append(("▶ now", f"{tech.price:,.1f}", ""))
    for name, lvl in below[:3]:
        rows.append((_LEVEL_SHORT.get(name, name), f"{lvl:,.1f}", _diff(lvl - tech.price)))
    return "📐 <b>KEY LEVELS</b>\n" + _pre(rows, right=(1, 2)) if len(rows) > 1 else ""


def format_full_report(tech: TechSnapshot, news: NewsScore, sig: Signal, briefing: str | None,
                       source: str, as_of: datetime, max_headlines: int = 3, hours: int = 36,
                       events: list | None = None, trade=None, sizing: Sizing | None = None) -> str:
    """/analyze – the update, then briefing, structure, key levels, three headlines, the next releases. ~25 lines."""
    sections = [format_pulse(tech, sig, trade, sizing, events, as_of, title="XAU/USD ANALYSIS")]
    if briefing:
        sections.append(f"🧠 <b>AI BRIEFING</b>\n{escape(briefing)}")
    st = format_structure(tech)
    if st:
        sections.append(st)
    kl = format_key_levels(tech)
    if kl:
        sections.append(kl)
    head = f"📰 <b>NEWS</b> · score {_m(f'{news.score:+d}')} · {escape(news.label.split(' / ')[0].lower())}"
    if news.drivers:
        sections.append(head + "\n" + "\n".join(headline_short(a) for a in news.drivers[:max(1, min(max_headlines, 3))]))
    else:
        sections.append(head + "\n<i>no relevant headlines yet</i>")
    if events:
        ev = []
        for e in events[:2]:
            flame = "🔥" if e.impact == "High" else "🔸"
            ev.append(f"{flame} {escape(e.title)} · {e.when.strftime('%a %H:%M UTC')} · {_countdown(e.minutes_away)}")
        sections.append("📅 <b>NEXT</b>\n" + "\n".join(ev))
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------
def format_alert_bias_change(prev_bias: str | None, sig: Signal, tech: TechSnapshot) -> str:
    was = escape(prev_bias.replace("_", " ").title()) if prev_bias else "n/a"
    parts = [f"🔔 <b>BIAS CHANGED</b> · {was} → {escape(BIAS_WORD.get(sig.bias, sig.bias))} {sig.emoji} {sig.confidence}%\n"
             f"Price <b>{tech.price:,.1f}</b> ({_chg(tech.change_24h_pct)})"]
    if sig.direction != "flat" and sig.stop is not None and sig.zone_lo is not None and sig.confidence >= MIN_CONF:
        parts.append(levels_block(zone_lo=sig.zone_lo, zone_hi=sig.zone_hi, sl=sig.stop, tp1=sig.target1, tp2=sig.target2))
        parts.append(wait_line(sig, sig.chase_limit, in_zone=(sig.entry_mode == "in-zone")))
    else:
        parts.append(no_call_lines(sig))
    return "\n\n".join(parts)


def format_alert_news(articles: list[Article]) -> str:
    return "🚨 <b>HIGH-IMPACT GOLD NEWS</b>\n" + "\n".join(headline_line(a) for a in articles[:3])


def format_alert_price(tech: TechSnapshot, move_pct: float, window_label: str) -> str:
    direction = "up" if move_pct > 0 else "down"
    icon = "🚀" if move_pct > 0 else "📉"
    return (f"{icon} <b>XAU/USD {direction} {abs(move_pct):.2f}% in the last {window_label}</b> → {tech.price:,.1f}\n"
            f"RSI {tech.rsi:.0f} · support {_num(tech.nearest_support())} · resistance {_num(tech.nearest_resistance())}")


# ---------------------------------------------------------------------------
# self-check (historical replay)
# ---------------------------------------------------------------------------
def _bt_block(name: str, b: dict) -> tuple:
    wr = f"{b['win_rate']:.0f}%" if b.get("win_rate") is not None else "–"
    avg = _r(b["avg_r"]) if b.get("avg_r") is not None else "–"
    return (name, str(b.get("count", 0)), wr, avg)


def _deep_section(deep: dict | None) -> str:
    """The multi-year replay, by year."""
    if not deep or not isinstance(deep, dict) or not deep.get("summary"):
        return ""
    s, per = deep["summary"], deep.get("period") or {}
    try:
        f = datetime.fromisoformat(per["from"]).strftime("%b %Y")
        t = datetime.fromisoformat(per["to"]).strftime("%b %Y")
        when = f"{f} → {t}"
    except (KeyError, ValueError, TypeError):
        when = "multi-year"
    wr = f"{s['win_rate']:.1f}%" if s.get("win_rate") is not None else "–"
    pf = "–" if s.get("profit_factor") is None else f"{s['profit_factor']:.2f}"
    rows = [("Period", when), ("Trades", f"{s.get('count', 0)} (from {s.get('setups', 0)} setups)"),
            ("Win rate", wr), ("Average / total", f"{_r(s['avg_r']) if s.get('avg_r') is not None else '–'} / {_r(s.get('total_r', 0.0), 1)}"),
            ("Profit factor", f"{pf} · max drawdown {s.get('max_drawdown_r', 0):.1f}R")]
    out = f"<b>DEEP CHECK — {escape(when)} of spot bars</b>\n{_pre(rows, right=())}"
    years = deep.get("by_year") or {}
    if years:
        trows = [("Year", "Trades", "Win", "Avg", "Total")] + [(k, str(v["count"]), f"{v['win_rate']:.0f}%" if v.get("win_rate") is not None else "–",
                                                                _r(v["avg_r"]) if v.get("avg_r") is not None else "–", _r(v["total_r"], 1))
                                                               for k, v in years.items() if v.get("count")]
        out += "\n" + _pre(trows, right=(1, 2, 3, 4))
    return out


def _variants_table(rows_v: list, title: str, complete: bool | None = None) -> str:
    """One row per management; profit factor / drawdown and the per-year totals (multi-year sweeps) underneath."""
    trows = [("", "Trades", "Win", "Avg", "Total")]
    detail = []
    for v in rows_v:
        b = v.get("summary") or {}
        if not b.get("count"):
            continue
        wr = f"{b['win_rate']:.0f}%" if b.get("win_rate") is not None else "–"
        trows.append((v["name"], str(b["count"]), wr, _r(b["avg_r"]) if b.get("avg_r") is not None else "–",
                      _r(b["total_r"], 1) if b.get("total_r") is not None else "–"))
        pf = f"PF {b['profit_factor']:.2f}" if b.get("profit_factor") is not None else "PF –"
        dd = f"DD {b['max_drawdown_r']:.1f}R" if b.get("max_drawdown_r") is not None else ""
        years = b.get("by_year") or {}
        yr = " · ".join(f"{y} {_r(x.get('total_r', 0.0), 1)}" for y, x in years.items() if x.get("count"))
        detail.append(f"• <b>{escape(v['name'])}</b> — {pf}{' · ' + dd if dd else ''}" + (f"\n   {yr}" if yr else ""))
    if len(trows) <= 1:
        return ""
    out = f"<b>{title}</b>\n{_pre(trows, right=(1, 2, 3, 4))}"
    if detail:
        out += "\n" + "\n".join(detail)
    if complete is False:
        out += "\n<i>Still running — more rows arrive as each replay finishes.</i>"
    return out


def format_backtest(bt: dict | None, on_start: bool = True, variants: dict | None = None, deep: dict | None = None,
                    deep_variants: dict | None = None) -> str:
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
    deep_text = _deep_section(deep)
    if deep_text:
        parts.append(deep_text)
    dv = _variants_table((deep_variants or {}).get("variants") or [], "OTHER MANAGEMENTS — multi-year", (deep_variants or {}).get("complete"))
    if dv:
        parts.append(dv)
    vt = _variants_table((variants or {}).get("variants") or [], "OTHER MANAGEMENTS — last 60 days", (variants or {}).get("complete"))
    if vt:
        parts.append(vt)
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

<b>Trade</b>
/signal — bias, confidence, entry zone, stop and targets
/signals — the live call or setup, recent results
/analyze — full picture: chart, AI briefing, structure, levels, news, calendar
/levels — support, resistance, pivots, session levels, fibs

<b>Market</b>
/price — price, trend, RSI, ATR
/news — top gold-moving headlines
/calendar — upcoming USD releases
/backtest — how the rules did on real bars (2 months, 3 years, alternatives)

<b>Settings</b>
/risk 5000 1 — account and % risk → lot size on every call
/took · /skipped — mark the latest call (your own scorecard)
/subscribe · /unsubscribe — the 15-minute report
/alerts on|off — bias changes, big moves, breaking news, pre-release warnings
/status — bot status

""" + DISCLAIMER
