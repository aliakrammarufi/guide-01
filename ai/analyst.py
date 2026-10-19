"""
AI market briefing (optional).

Given the technical snapshot, the news score and the most influential
headlines, asks Claude for a short written briefing.  If no API key is
configured, or the call fails, ``write_briefing`` returns ``None`` and the
bot simply sends the rule-based report without a narrative.
"""
from __future__ import annotations

import logging

from market.analysis import TechSnapshot
from news.sentiment import NewsScore

log = logging.getLogger(__name__)

SYSTEM = """You are a concise, no-hype gold (XAU/USD) market analyst writing for a trader on Telegram.
You receive a technical snapshot and the most relevant scored headlines.

Write a briefing of at most 120 words, plain text (no markdown headers, no bullet symbols other than '•'):
1. One-line bias: bullish / bearish / neutral and why (tech + news agreement or conflict).
2. 2-3 key drivers from the news (name the theme, be specific).
3. What to watch next (the nearest levels or upcoming event mentioned in the news).
4. One risk / invalidation note.

Never promise outcomes, never give position sizes, never say "guaranteed". This is decision support only."""


def _fmt_level(x: float | None) -> str:
    return f"{x:,.1f}" if x is not None else "n/a"


def build_context(tech: TechSnapshot, news: NewsScore, events: list | None = None, max_headlines: int = 10) -> str:
    lines = [
        f"Price: {tech.price:,.2f}  (1h {tech.change_1h_pct:+.2f}%, 24h {tech.change_24h_pct:+.2f}%, ~1w {tech.change_7d_pct:+.2f}%)",
        f"Technical score: {tech.score:+d}/100 ({tech.trend_label})",
        f"H1 EMA20/50/200: {tech.ema20:,.1f} / {tech.ema50:,.1f} / {tech.ema200:,.1f}",
        f"Daily EMA50/200: {tech.daily_ema50:,.1f} / {tech.daily_ema200:,.1f}",
        f"RSI14: {tech.rsi:.1f}  MACD hist: {tech.macd_hist:+.2f}  ATR14: {tech.atr:.2f}  Bollinger %B: {tech.bb_pct:.2f}",
        f"Nearest support: {_fmt_level(tech.nearest_support())}  Nearest resistance: {_fmt_level(tech.nearest_resistance())}",
        "Technical reasons: " + "; ".join(tech.reasons),
        "Technical flags: " + ("; ".join(tech.flags) if tech.flags else "none"),
        f"News score: {news.score:+d}/100 ({news.label}); {news.n_bullish} bullish / {news.n_bearish} bearish / {news.n_neutral} neutral headlines",
        "Top themes: " + ", ".join(f"{t} ({v:+.1f})" for t, v in news.top_themes),
        "Headlines (impact, age):",
    ]
    for a in news.drivers[:max_headlines]:
        lines.append(f"  [{a.impact:+.1f}, {a.age_hours:.0f}h] {a.title} — {a.source} ({a.reason or ''})")
    if events:
        lines.append("Upcoming scheduled releases (next 48h):")
        for e in events[:8]:
            h = e.minutes_away / 60.0
            extra = f" forecast {e.forecast} prev {e.previous}" if e.forecast or e.previous else ""
            lines.append(f"  in {h:.1f}h: {e.title} ({e.currency}, {e.impact}){extra}")
    return "\n".join(lines)


def write_briefing(api_key: str, model: str, tech: TechSnapshot, news: NewsScore, events: list | None = None) -> str | None:
    if not api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_context(tech, news, events)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        log.warning("AI briefing failed: %s", exc)
        return None
