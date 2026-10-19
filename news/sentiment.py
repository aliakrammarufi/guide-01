"""
News sentiment for gold.

Two scorers:

* ``lexicon_score``  – a rule-based scorer that understands the main gold
  drivers (rates, dollar, yields, inflation, geopolitics, central-bank
  demand, flows).  Always available, no API key.
* ``ClaudeClassifier`` – sends batches of headlines to the Anthropic API and
  gets a structured impact rating back.  Used when ANTHROPIC_API_KEY is set;
  falls back to the lexicon on any error.

Impact scale (both scorers): -2 strongly bearish for gold … +2 strongly
bullish.  ``aggregate`` turns a list of scored articles into a single
-100..+100 news score with time decay.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass

from news.store import Article

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lexicon
# ---------------------------------------------------------------------------
# (regex, weight, theme).  Weights add up and are clipped to [-2, 2].
# Subject-aware rules come first so that "dollar falls" and "gold falls"
# are scored differently.
_DIR_DOWN = r"(?:fall(?:s|ing)?|fell|drop(?:s|ped|ping)?|slide(?:s)?|slid(?:ing)?|slip(?:s|ping)?|slump(?:s|ing)?|tumbl(?:e|es|ing)|plung(?:e|es|ing)|sink(?:s|ing)?|retreat(?:s|ing)?|dip(?:s|ping)?|lower|weaken(?:s|ing)?|eas(?:e|es|ing)|declin(?:e|es|ing)|los(?:e|es|ing)|sell-?off|pullback|pulls? back)"
_DIR_UP = r"(?:ris(?:e|es|ing)|rose|climb(?:s|ing)?|jump(?:s|ing)?|surg(?:e|es|ing)|soar(?:s|ing)?|rall(?:y|ies|ying)|gain(?:s|ing)?|advanc(?:e|es|ing)|strengthen(?:s|ing)?|higher|firm(?:s|ing)?|extends? (?:gains|rally|record)|hits? (?:a )?(?:new |fresh )?(?:record|all-time) high|record high|all-time high)"
_GOLD = r"(?:gold|bullion|xau|precious metals?|metals?)"
_USD = r"(?:dollar|dxy|greenback|usd)"
_YIELDS = r"(?:yields?|treasur(?:y|ies)|bond yields?|10-year)"
_ANY_SUBJECT = r"(?:gold|bullion|xau|metals?|dollar|dxy|greenback|usd|yields?|treasur(?:y|ies)|stocks?|equities|oil)"


def _subj_dir(subject: str, direction: str, max_gap: int) -> re.Pattern[str]:
    """``subject … direction`` where the gap contains no other market subject.

    Keeps "gold hits record high as dollar slides" from counting "gold … slides".
    """
    gap = rf"(?:(?!\b{_ANY_SUBJECT}\b)[^.;]){{0,{max_gap}}}?"
    return re.compile(rf"\b{subject}\b{gap}\b{direction}\b")


RULES: list[tuple[re.Pattern[str], float, str]] = [
    # --- subject + direction (gold itself = momentum, dollar/yields = inverse)
    (_subj_dir(_GOLD, _DIR_UP, 50), +0.8, "momentum"),
    (_subj_dir(_GOLD, _DIR_DOWN, 50), -0.8, "momentum"),
    (re.compile(rf"\b(?:sends?|pushes?|drives?|knocks?)\s+{_GOLD}\s+(?:lower|down|tumbling|sliding|falling)"), -0.8, "momentum"),
    (re.compile(rf"\b(?:sends?|pushes?|drives?|lifts?|boosts?)\s+{_GOLD}\s+(?:higher|up|soaring|surging|to (?:a )?record)"), +0.8, "momentum"),
    (_subj_dir(_USD, _DIR_DOWN, 40), +0.9, "dollar"),
    (_subj_dir(_USD, _DIR_UP, 40), -0.9, "dollar"),
    (re.compile(rf"\b(?:weak|weaker|soft|softer)\s+{_USD}\b"), +0.9, "dollar"),
    (re.compile(rf"\b(?:strong|stronger|firm|firmer|resurgent)\s+{_USD}\b|\blifts? (?:the )?{_USD}\b"), -0.9, "dollar"),
    (_subj_dir(_YIELDS, _DIR_DOWN, 40), +0.9, "yields"),
    (_subj_dir(_YIELDS, _DIR_UP, 40), -0.9, "yields"),
    (re.compile(rf"\b(?:lower|falling|sliding)\s+{_YIELDS}\b"), +0.9, "yields"),
    (re.compile(rf"\b(?:higher|rising|surging)\s+{_YIELDS}\b"), -0.9, "yields"),
    # --- Fed / rates
    (re.compile(r"\b(?:rate cuts?|cuts? (?:interest )?rates?|cut rates|lower(?:s|ing)? (?:interest )?rates|easing cycle|dovish|monetary easing|stimulus|quantitative easing|\bqe\b)"), +1.4, "fed"),
    # "dents rate-cut hopes", "rate cut bets fade" → cancels the rule above and then some
    (re.compile(r"\b(?:dent(?:s|ed)?|dim(?:s|med)?|fad(?:e|es|ing)|reduc(?:e|es|ed)|damp(?:en|ens|ened)|par(?:e|es|ed) back|scal(?:e|es|ed) back|trim(?:s|med)?|cool(?:s|ed)?|hurts?|kill(?:s|ed)?)\s+(?:(?!\.)[^.]){0,25}?(?:rate[- ]cut|easing)\s+(?:hopes|bets|expectations|odds|wagers)|(?:rate[- ]cut|easing)\s+(?:hopes|bets|expectations|odds|wagers)\s+(?:fade|fades|faded|dim|dims|dimmed|recede|receded|cool|cooled|unwind|unwound)"), -2.4, "fed"),
    (re.compile(r"\b(?:rate hikes?|hikes? (?:interest )?rates?|raise[sd]? (?:interest )?rates|hawkish|higher for longer|higher-for-longer|tightening|delay(?:s|ed|ing)? (?:rate )?cuts|fewer (?:rate )?cuts|no (?:rate )?cuts?)"), -1.4, "fed"),
    (re.compile(r"\b(?:holds? rates? (?:steady|unchanged)|pause[sd]? )"), -0.3, "fed"),
    # --- inflation / data
    (re.compile(r"\b(?:inflation (?:cools|cooled|eases|eased|slows|slowed|softens|softer)|cooler[- ]than[- ]expected|softer[- ]than[- ]expected|cpi (?:cools|eases|slows|misses)|disinflation)"), +0.8, "inflation"),
    (re.compile(r"\b(?:inflation (?:hot|hotter|rises|rose|jumps|accelerates|sticky|surprises)|hotter[- ]than[- ]expected|hot cpi|cpi (?:beats|jumps|hot|hotter)|sticky inflation|stubborn inflation)"), -0.6, "inflation"),
    (re.compile(r"\b(?:inflation (?:fears|worries|concerns|hedge)|hedge against inflation|stagflation)"), +0.6, "inflation"),
    (re.compile(r"\b(?:strong(?:er)? (?:jobs|payrolls|employment|economy|growth)|jobs (?:beat|blowout|surge)|payrolls (?:beat|jump|surge)|robust (?:economy|hiring|data)|gdp beats|upbeat data|solid data)"), -0.8, "economy"),
    (re.compile(r"\b(?:weak(?:er)? (?:jobs|payrolls|employment|data|economy)|jobs (?:miss|disappoint|slow)|payrolls (?:miss|slow)|unemployment (?:rises|jumps|climbs)|jobless claims (?:rise|jump)|recession|slowdown|contraction|layoffs)"), +0.8, "economy"),
    # --- geopolitics / risk
    (re.compile(r"\b(?:safe[- ]haven|haven demand|flight to safety|geopolitic|tensions?|conflict|war\b|warfare|invasion|invades?|missile|airstrike|air strike|attack|escalat|military|troops|nuclear|hostilit)"), +1.0, "geopolitics"),
    (re.compile(r"\b(?:ceasefire|cease-fire|truce|peace (?:deal|talks|agreement)|de-?escalat|risk[- ]on|risk appetite|stocks rally|equities rally|record high for stocks)"), -0.8, "risk"),
    (re.compile(r"\b(?:risk[- ]off|risk aversion|stocks (?:tumble|plunge|slump|sell-?off)|market turmoil|crisis|bank(?:ing)? (?:crisis|collapse|failure)|credit event)"), +0.8, "risk"),
    (re.compile(r"\b(?:tariffs?|trade war|trade tensions?|sanctions?|debt ceiling|government shutdown|default)"), +0.6, "policy"),
    # --- demand / flows
    (re.compile(r"\b(?:central banks?\b[^.]{0,30}?\b(?:buy|buys|buying|bought|purchas\w*|demand|adds?|added|accumulat\w*)|pboc|gold buying|buying streak|reserve (?:buying|purchases|diversification)|gold reserves? (?:rise|rose|increase)|bullion demand|physical demand|jewel(?:le)?ry demand|india(?:n)? demand|china(?:'s)? demand|de-?dollari[sz]ation)"), +0.9, "demand"),
    (re.compile(r"\b(?:etf inflows?|inflows? into gold|fund inflows?)"), +0.7, "flows"),
    (re.compile(r"\b(?:etf outflows?|outflows? from gold|fund outflows?|liquidation|profit[- ]taking|overbought|margin calls?)"), -0.7, "flows"),
    (re.compile(r"\b(?:bullish|upgrade[sd]? (?:gold )?(?:forecast|target)|raises? (?:gold )?(?:price )?(?:forecast|target)|buy the dip)"), +0.5, "analyst"),
    (re.compile(r"\b(?:bearish|downgrade[sd]? (?:gold )?(?:forecast|target)|cuts? (?:gold )?(?:price )?(?:forecast|target)|correction)"), -0.5, "analyst"),
]


@dataclass
class ScoreResult:
    impact: float
    confidence: float
    theme: str
    reason: str
    scored_by: str


def lexicon_score(text: str) -> ScoreResult:
    t = text.lower()
    total = 0.0
    hits: list[tuple[str, float]] = []
    for pattern, weight, theme in RULES:
        if pattern.search(t):
            total += weight
            hits.append((theme, weight))
    impact = max(-2.0, min(2.0, total))
    if not hits:
        return ScoreResult(0.0, 0.25, "other", "no gold-driver keywords matched", "lexicon")
    # Dominant theme = largest absolute contribution
    theme_tot: dict[str, float] = {}
    for theme, w in hits:
        theme_tot[theme] = theme_tot.get(theme, 0.0) + w
    theme = max(theme_tot, key=lambda k: abs(theme_tot[k]))
    conf = min(0.75, 0.35 + 0.12 * len(hits))
    signs = {("+" if w > 0 else "-") + th for th, w in hits}
    reason = "keywords: " + ", ".join(sorted(signs))
    return ScoreResult(impact, conf, theme, reason, "lexicon")


def apply_lexicon(articles: list[Article]) -> list[Article]:
    for a in articles:
        r = lexicon_score(f"{a.title}. {a.summary}")
        a.impact, a.confidence, a.theme, a.reason, a.scored_by = r.impact, r.confidence, r.theme, r.reason, r.scored_by
    return articles


# ---------------------------------------------------------------------------
# Claude classifier
# ---------------------------------------------------------------------------
CLASSIFIER_SYSTEM = """You are a senior precious-metals analyst. You rate how each news headline is
likely to move the gold price (XAU/USD) over the next 1-3 days.

Scale: -2 strongly bearish for gold, -1 bearish, 0 neutral/irrelevant, +1 bullish, +2 strongly bullish.
Think about the real transmission channels: Fed policy & rate expectations, US dollar, real yields,
inflation, growth/jobs data, geopolitical risk & safe-haven demand, central-bank buying, ETF flows,
risk sentiment in equities, and gold's own momentum (record highs, profit taking).
Remember: a weaker dollar or lower yields is BULLISH for gold; strong jobs data or hawkish Fed talk is BEARISH.

Respond with ONLY a JSON array, one object per headline, same order, no prose:
[{"id": "<id>", "impact": <-2..2>, "confidence": <0..1>, "theme": "<fed|dollar|yields|inflation|economy|geopolitics|risk|demand|flows|policy|momentum|other>", "reason": "<max 12 words>"}]"""


class ClaudeClassifier:
    def __init__(self, api_key: str, model: str):
        import anthropic  # lazy import so the lexicon path has no SDK dependency

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def score(self, articles: list[Article], batch_size: int = 25) -> list[Article]:
        for i in range(0, len(articles), batch_size):
            batch = articles[i : i + batch_size]
            try:
                self._score_batch(batch)
            except Exception as exc:  # noqa: BLE001
                log.warning("Claude classification failed (%s); using lexicon for this batch", exc)
                apply_lexicon(batch)
        return articles

    def _score_batch(self, batch: list[Article]) -> None:
        lines = [
            {"id": a.id, "headline": a.title, "snippet": a.summary[:200], "source": a.source}
            for a in batch
        ]
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=CLASSIFIER_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(lines, ensure_ascii=False)}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        parsed = _extract_json_array(text)
        by_id = {str(item.get("id")): item for item in parsed if isinstance(item, dict)}
        for a in batch:
            item = by_id.get(a.id)
            if not item:
                r = lexicon_score(f"{a.title}. {a.summary}")
                a.impact, a.confidence, a.theme, a.reason, a.scored_by = r.impact, r.confidence, r.theme, r.reason, r.scored_by
                continue
            a.impact = max(-2.0, min(2.0, float(item.get("impact", 0))))
            a.confidence = max(0.0, min(1.0, float(item.get("confidence", 0.6))))
            a.theme = str(item.get("theme", "other"))[:20]
            a.reason = str(item.get("reason", ""))[:120]
            a.scored_by = "claude"


def _extract_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                pass
    raise ValueError("classifier returned no JSON array")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
@dataclass
class NewsScore:
    score: int                 # -100 .. +100
    n_articles: int
    n_bullish: int
    n_bearish: int
    n_neutral: int
    top_themes: list[tuple[str, float]]   # (theme, net weighted impact)
    drivers: list[Article]     # most influential articles, strongest first

    @property
    def label(self) -> str:
        if self.score >= 40:
            return "Strongly bullish news flow"
        if self.score >= 15:
            return "Bullish news flow"
        if self.score <= -40:
            return "Strongly bearish news flow"
        if self.score <= -15:
            return "Bearish news flow"
        return "Mixed / quiet news flow"


_STOP = {"the", "a", "an", "of", "to", "in", "on", "as", "and", "for", "at", "by", "with", "from", "is", "are", "after",
         "amid", "over", "says", "say", "could", "may", "will", "its", "it", "this", "that", "be", "has", "have"}


def _dupe_key(title: str) -> str:
    """Syndicated copies of one story share their first few meaningful words."""
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split() if w not in _STOP]
    return " ".join(words[:8]) if len(words) >= 5 else ""


def aggregate(articles: list[Article], half_life_hours: float = 10.0, scale: float = 0.9) -> NewsScore:
    """Combine article impacts into one score with exponential time decay.

    Near-duplicate headlines (the same story republished by several outlets) count once.
    The score is a *shrunk average* of the weighted impacts, so 150 mildly bullish
    headlines read as "bullish", not "+100": score = 100·tanh(Σw / (scale·(n_directional+5))).
    With few articles the n+5 term keeps a single headline from looking like a trend.
    """
    total = 0.0
    bull = bear = neutral = 0
    themes: dict[str, float] = {}
    weighted: list[tuple[float, Article]] = []
    seen: set[str] = set()
    for a in sorted(articles, key=lambda x: x.published_at, reverse=True):
        if a.impact is None:
            continue
        key = _dupe_key(a.title)
        if key and key in seen:
            continue
        seen.add(key)
        decay = 0.5 ** (a.age_hours / half_life_hours) if half_life_hours > 0 else 1.0
        w = a.impact * (a.confidence if a.confidence is not None else 0.5) * decay
        total += w
        weighted.append((w, a))
        if a.direction == "bullish":
            bull += 1
        elif a.direction == "bearish":
            bear += 1
        else:
            neutral += 1
        if a.theme and a.direction != "neutral":
            themes[a.theme] = themes.get(a.theme, 0.0) + w
    n = len(weighted)
    n_dir = bull + bear                      # neutral items carry no direction, so they don't dilute the score
    score = int(round(100.0 * math.tanh(total / (scale * (n_dir + 5))))) if (scale > 0 and n_dir) else 0
    top_themes = sorted(themes.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]
    drivers = [a for _, a in sorted(weighted, key=lambda t: abs(t[0]), reverse=True)]
    return NewsScore(
        score=score,
        n_articles=n,
        n_bullish=bull,
        n_bearish=bear,
        n_neutral=neutral,
        top_themes=top_themes,
        drivers=drivers,
    )


CORE_TITLE_TERMS = (
    "gold", "xau", "bullion", "precious metals?",
    "fed", "fomc", "powell", "rate cuts?", "rate hikes?", "rate decision", "interest rates?",
    "cpi", "inflation", "payrolls?", "nonfarm", "non-farm", "nfp", "jobs report", "gdp", "pce",
    "treasur(?:y|ies)", "yields?", "dollar", "dxy", "greenback",
    "tariffs?", "trade war", "sanctions?", "war", "missiles?", "ceasefire", "invasion", "airstrikes?",
    "central banks?", "shutdown", "default", "jackson hole", "recession", "safe[- ]haven",
)
_CORE_RE = re.compile(r"\b(?:" + "|".join(CORE_TITLE_TERMS) + r")\b", re.I)


def _term_re(terms: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(t.strip()) for t in terms if t.strip()) + r")\b", re.I)


def is_high_impact(article: Article, terms: list[str]) -> bool:
    """High-impact = the headline itself is about a gold driver AND it carries a market-moving term
    (or the classifier scored it strongly). Whole words only ('warns' is not 'war', 'toward' is not 'war');
    the summary alone never qualifies a story."""
    if not _CORE_RE.search(article.title or ""):
        return False
    text = f"{article.title} {article.summary[:160]}"
    if terms and _term_re(terms).search(text):
        return True
    return article.impact is not None and abs(article.impact) >= 1.5 and (article.confidence or 0) >= 0.6
