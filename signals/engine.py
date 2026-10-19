"""
Signal engine: combines the technical score and the news score into one
bias with a confidence level, plus ATR-based reference levels.

This is decision support.  The bot never places orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from market.analysis import TechSnapshot
from news.sentiment import NewsScore


@dataclass
class Signal:
    bias: str                 # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    combined: int             # -100 .. +100
    tech_score: int
    news_score: int
    confidence: int           # 0 .. 100
    agreement: str            # "aligned" | "conflict" | "one-sided"
    price: float
    atr: float
    stop: float | None
    target1: float | None
    target2: float | None
    nearest_support: float | None
    nearest_resistance: float | None
    notes: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def direction(self) -> str:
        if self.bias in ("STRONG_BUY", "BUY"):
            return "long"
        if self.bias in ("STRONG_SELL", "SELL"):
            return "short"
        return "flat"

    @property
    def emoji(self) -> str:
        return {
            "STRONG_BUY": "🟢🟢",
            "BUY": "🟢",
            "NEUTRAL": "⚪",
            "SELL": "🔴",
            "STRONG_SELL": "🔴🔴",
        }[self.bias]

    @property
    def label(self) -> str:
        return {
            "STRONG_BUY": "Strong bullish bias",
            "BUY": "Bullish bias",
            "NEUTRAL": "Neutral / wait",
            "SELL": "Bearish bias",
            "STRONG_SELL": "Strong bearish bias",
        }[self.bias]

    def to_dict(self) -> dict:
        return {
            "bias": self.bias,
            "combined": self.combined,
            "tech_score": self.tech_score,
            "news_score": self.news_score,
            "confidence": self.confidence,
            "price": self.price,
            "generated_at": self.generated_at.isoformat(),
        }


def _bias_from_score(score: int) -> str:
    if score >= 55:
        return "STRONG_BUY"
    if score >= 25:
        return "BUY"
    if score <= -55:
        return "STRONG_SELL"
    if score <= -25:
        return "SELL"
    return "NEUTRAL"


def build_signal(
    tech: TechSnapshot,
    news: NewsScore,
    tech_weight: float = 0.6,
    news_weight: float = 0.4,
    atr_stop_mult: float = 1.5,
    atr_target_mult: float = 2.5,
) -> Signal:
    total_w = tech_weight + news_weight
    tw, nw = tech_weight / total_w, news_weight / total_w

    # With very little news, lean more on technicals
    if news.n_articles < 3:
        nw *= news.n_articles / 3.0
        tw = 1.0 - nw

    combined = int(round(tw * tech.score + nw * news.score))
    bias = _bias_from_score(combined)
    notes: list[str] = []

    # Agreement between the two sources drives confidence
    same_sign = (tech.score > 0) == (news.score > 0)
    both_meaningful = abs(tech.score) >= 15 and abs(news.score) >= 15
    if both_meaningful and same_sign:
        agreement = "aligned"
        notes.append("Technicals and news agree")
    elif both_meaningful and not same_sign:
        agreement = "conflict"
        notes.append("Technicals and news disagree – expect chop, size down")
    else:
        agreement = "one-sided"
        if abs(news.score) < 15:
            notes.append("News flow is quiet/mixed; signal is mostly technical")
        else:
            notes.append("Technicals are mixed; signal is mostly news-driven")

    confidence = min(100, int(abs(combined)))
    if agreement == "aligned":
        confidence = min(100, confidence + 15)
    elif agreement == "conflict":
        confidence = max(0, confidence - 25)
    if tech.flags:
        confidence = max(0, confidence - 5 * len(tech.flags))
        notes.extend(tech.flags)

    # Reference levels (ATR based, aligned with the bias direction)
    stop = t1 = t2 = None
    if bias in ("BUY", "STRONG_BUY"):
        stop = tech.price - atr_stop_mult * tech.atr
        t1 = tech.price + atr_target_mult * 0.5 * tech.atr
        t2 = tech.price + atr_target_mult * tech.atr
        sup = tech.nearest_support()
        if sup is not None and sup > stop:
            stop = min(stop, sup - 0.25 * tech.atr)  # park the stop under structure
    elif bias in ("SELL", "STRONG_SELL"):
        stop = tech.price + atr_stop_mult * tech.atr
        t1 = tech.price - atr_target_mult * 0.5 * tech.atr
        t2 = tech.price - atr_target_mult * tech.atr
        res = tech.nearest_resistance()
        if res is not None and res < stop:
            stop = max(stop, res + 0.25 * tech.atr)

    return Signal(
        bias=bias,
        combined=combined,
        tech_score=tech.score,
        news_score=news.score,
        confidence=confidence,
        agreement=agreement,
        price=tech.price,
        atr=tech.atr,
        stop=stop,
        target1=t1,
        target2=t2,
        nearest_support=tech.nearest_support(),
        nearest_resistance=tech.nearest_resistance(),
        notes=notes,
    )


def bias_changed(previous: dict | None, current: Signal) -> bool:
    """True when the bias category changed (used for change alerts)."""
    if not previous:
        return True
    return previous.get("bias") != current.bias
