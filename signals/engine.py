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
    # ---- entry plan (None when the bias is flat) ----
    zone_lo: float | None = None      # pullback zone – wait for price to come here
    zone_hi: float | None = None
    chase_limit: float | None = None  # if price runs past this without a pullback, the setup is missed
    entry_mode: str = ""              # "pullback" (price above the zone) | "in-zone" (price already there)
    session: dict | None = None       # session verdict used for this signal
    raw_confidence: int = 0           # confidence before the session adjustment
    confluence: list[str] = field(default_factory=list)   # what else sits at the zone (fib, OB, PDL, VWAP…)

    @property
    def direction(self) -> str:
        if self.bias in ("STRONG_BUY", "BUY"):
            return "long"
        if self.bias in ("STRONG_SELL", "SELL"):
            return "short"
        return "flat"

    @property
    def zone_mid(self) -> float | None:
        if self.zone_lo is None or self.zone_hi is None:
            return None
        return (self.zone_lo + self.zone_hi) / 2.0

    @property
    def planned_rr(self) -> float | None:
        """Reward:risk from the middle of the zone to target 2."""
        mid = self.zone_mid
        if mid is None or self.stop is None or self.target2 is None:
            return None
        risk = abs(mid - self.stop)
        return round(abs(self.target2 - mid) / risk, 2) if risk else None

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
            "zone_lo": self.zone_lo, "zone_hi": self.zone_hi, "chase_limit": self.chase_limit,
            "entry_mode": self.entry_mode, "planned_rr": self.planned_rr, "confluence": list(self.confluence),
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


def plan_entry(direction: str, tech: TechSnapshot, atr_stop_mult: float = 1.5, atr_target_mult: float = 2.5, refine: bool = True) -> dict:
    """Where to get in, where it is wrong, where to take profit.

    Long: the pullback zone sits between the H1 EMA20 (upper edge) and the nearest
    support / EMA50 (lower edge). The stop goes under the zone and under structure;
    targets are measured from the middle of the zone, and target 1 is pulled in front
    of a nearby resistance so profit is booked before the wall. Mirrored for shorts.

    Price action then refines the zone: a 50 / 61.8 % retracement of the last impulse or
    an unmitigated order block just beyond the zone pulls its far edge out to include it
    (bounded), and everything that coincides with the zone – fib level, order block, fair
    value gap, previous-day/week level, Asian range, VWAP, round number, EMA50, swing
    level – is listed as *confluence* so the trader can judge the zone's quality.
    """
    price, atr = tech.price, max(tech.atr, 1e-6)
    out: dict = {}
    extended = abs(price - tech.ema20) > 1.0 * atr      # price has run far from the EMA20: use a shallow pullback instead
    if direction == "long":
        sup = tech.nearest_support()
        if extended and price > tech.ema20:
            hi, lo = price - 0.3 * atr, max(price - 0.9 * atr, tech.ema20)
        else:
            hi = tech.ema20 if tech.ema20 < price else price - 0.25 * atr
            candidates = [price - 1.2 * atr, hi - 0.9 * atr]
            if tech.ema50 < hi:
                candidates.append(tech.ema50)
            if sup is not None and sup < hi:
                candidates.append(sup + 0.1 * atr)
            lo = max(candidates)
        if hi - lo < 0.3 * atr:
            lo = hi - 0.3 * atr
        lo, confluence = _refine_zone("long", lo, hi, tech, atr, move=refine)
        mid = (lo + hi) / 2
        stop = lo - 0.5 * atr
        if sup is not None and sup < hi:
            stop = min(stop, sup - 0.25 * atr)
        stop = max(stop, mid - atr_stop_mult * atr)        # never wider than the ATR stop
        t1 = mid + atr_target_mult * 0.5 * atr
        t2 = mid + atr_target_mult * atr
        res = tech.nearest_resistance()
        if res is not None and mid + 0.6 * atr < res < t1:
            t1 = res - 0.1 * atr
        chase = max(hi + 0.6 * atr, price + 0.4 * atr)     # always leaves room above the current price
        mode = "in-zone" if lo <= price <= hi + 0.05 * atr else "pullback"
    elif direction == "short":
        res = tech.nearest_resistance()
        if extended and price < tech.ema20:
            lo, hi = price + 0.3 * atr, min(price + 0.9 * atr, tech.ema20)
        else:
            lo = tech.ema20 if tech.ema20 > price else price + 0.25 * atr
            candidates = [price + 1.2 * atr, lo + 0.9 * atr]
            if tech.ema50 > lo:
                candidates.append(tech.ema50)
            if res is not None and res > lo:
                candidates.append(res - 0.1 * atr)
            hi = min(candidates)
        if hi - lo < 0.3 * atr:
            hi = lo + 0.3 * atr
        hi, confluence = _refine_zone("short", lo, hi, tech, atr, move=refine)
        mid = (lo + hi) / 2
        stop = hi + 0.5 * atr
        if res is not None and res > lo:
            stop = max(stop, res + 0.25 * atr)
        stop = min(stop, mid + atr_stop_mult * atr)
        t1 = mid - atr_target_mult * 0.5 * atr
        t2 = mid - atr_target_mult * atr
        sup = tech.nearest_support()
        if sup is not None and t1 < sup < mid - 0.6 * atr:
            t1 = sup + 0.1 * atr
        chase = min(lo - 0.6 * atr, price - 0.4 * atr)
        mode = "in-zone" if lo - 0.05 * atr <= price <= hi else "pullback"
    else:
        return out
    out.update(zone_lo=round(lo, 2), zone_hi=round(hi, 2), stop=round(stop, 2), target1=round(t1, 2),
               target2=round(t2, 2), chase_limit=round(chase, 2), entry_mode=mode, confluence=confluence)
    return out


_LEVEL_LABEL = {"PDH": "prev-day high", "PDL": "prev-day low", "PWH": "prev-week high", "PWL": "prev-week low",
                "ASIA_H": "Asia high", "ASIA_L": "Asia low", "VWAP": "daily VWAP", "ROUND_BELOW": "round number", "ROUND_ABOVE": "round number"}


def _refine_zone(direction: str, lo: float, hi: float, tech: TechSnapshot, atr: float, move: bool = True) -> tuple[float, list[str]]:
    """Pull the far edge of the zone out to a fib 50/61.8 or an order block just beyond it (only when
    ``move``), and list everything that coincides with the zone. Returns (new far edge, confluence)."""
    pa = tech.pa
    long = direction == "long"
    far = lo if long else hi
    near = hi if long else lo
    max_depth = 1.5 * atr                       # the zone never gets deeper than this from its near edge
    tol = 0.25 * atr
    conf: list[str] = []

    def inside(level: float, pad: float = tol) -> bool:
        a, b = (lo - pad, hi + pad)
        return a <= level <= b

    def beyond_but_close(level: float) -> bool:    # just past the far edge, within reach
        if not move:
            return False
        return (far - 0.5 * atr <= level < far) if long else (far < level <= far + 0.5 * atr)

    if pa is not None:
        # fib retracements of the last impulse in our direction
        imp = pa.impulse
        if imp and imp["direction"] == ("up" if long else "down"):
            for name, lvl in imp["levels"].items():
                if name in ("50", "61.8") and beyond_but_close(lvl):
                    far = lvl
                if inside(lvl, tol) or (name in ("50", "61.8") and lvl == far):
                    conf.append(f"{name}% fib")
        # order blocks / fair value gaps of our kind
        kind = "bullish" if long else "bearish"
        for ob in pa.order_blocks:
            if ob["kind"] != kind:
                continue
            mid_ob = (ob["lo"] + ob["hi"]) / 2
            if beyond_but_close(mid_ob):
                far = ob["lo"] if long else ob["hi"]
            if inside(mid_ob, tol) or (long and ob["lo"] == far) or (not long and ob["hi"] == far):
                conf.append("order block")
                break
        for g in pa.fvgs:
            if g["kind"] == kind and inside((g["lo"] + g["hi"]) / 2, tol):
                conf.append("fair value gap")
                break
        # key levels
        for key, lvl in pa.levels.items():
            if lvl is None:
                continue
            if long and key in ("PDH", "PWH", "ASIA_H", "ROUND_ABOVE"):
                continue
            if not long and key in ("PDL", "PWL", "ASIA_L", "ROUND_BELOW"):
                continue
            if inside(float(lvl), tol):
                conf.append(_LEVEL_LABEL.get(key, key))
    # bound the depth, keep the ordering sane
    if long:
        far = max(far, near - max_depth)
        lo = min(far, hi - 0.3 * atr)
    else:
        far = min(far, near + max_depth)
        hi = max(far, lo + 0.3 * atr)
    # moving averages and swing levels inside the zone
    if lo - tol <= tech.ema50 <= hi + tol:
        conf.append("EMA50")
    if lo - tol <= tech.ema20 <= hi + tol:
        conf.append("EMA20")
    lvl = tech.nearest_support() if long else tech.nearest_resistance()
    if lvl is not None and lo - tol <= lvl <= hi + tol:
        conf.append("swing " + ("support" if long else "resistance"))
    # de-duplicate, keep order
    seen: set[str] = set()
    conf = [c for c in conf if not (c in seen or seen.add(c))]
    return (lo if long else hi), conf


def build_signal(
    tech: TechSnapshot,
    news: NewsScore,
    tech_weight: float = 0.6,
    news_weight: float = 0.4,
    atr_stop_mult: float = 1.5,
    atr_target_mult: float = 2.5,
    session=None,
    zone_refine: bool = True,
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

    raw_confidence = confidence
    session_dict = None
    if session is not None:
        session_dict = session.to_dict()
        confidence = max(0, min(100, confidence + session.conf_adj))
        if session.reasons:
            notes.append(session.reasons[0])

    # Entry plan: pullback zone, stop beyond it, targets from the zone middle
    direction = "long" if bias in ("BUY", "STRONG_BUY") else "short" if bias in ("SELL", "STRONG_SELL") else "flat"
    plan = plan_entry(direction, tech, atr_stop_mult, atr_target_mult, refine=zone_refine)
    stop, t1, t2 = plan.get("stop"), plan.get("target1"), plan.get("target2")

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
        zone_lo=plan.get("zone_lo"), zone_hi=plan.get("zone_hi"), chase_limit=plan.get("chase_limit"),
        entry_mode=plan.get("entry_mode", ""), session=session_dict, raw_confidence=raw_confidence,
        confluence=list(plan.get("confluence") or []),
    )


def bias_changed(previous: dict | None, current: Signal) -> bool:
    """True when the bias category changed (used for change alerts)."""
    if not previous:
        return True
    return previous.get("bias") != current.bias
