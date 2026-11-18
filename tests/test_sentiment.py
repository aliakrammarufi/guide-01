from datetime import datetime, timedelta, timezone

from news.fetch import article_id, is_relevant, normalize_title, parse_feed
from news.sentiment import _extract_json_array, aggregate, apply_lexicon, is_high_impact, lexicon_score
from news.store import Article

BULLISH = [
    "Gold hits record high as dollar slides on Fed rate cut bets",
    "US CPI cools more than expected, boosting rate cut hopes",
    "Gold climbs on safe-haven demand after missile strike escalates tensions",
    "China central bank extends gold buying streak for 11th month",
    "Treasury yields fall after weak jobs data",
]
BEARISH = [
    "Gold falls as strong jobs data lifts dollar, Treasury yields rise",
    "Fed signals higher for longer; hawkish Powell sends gold tumbling",
    "Ceasefire deal boosts risk appetite, gold retreats from record",
    "Hot CPI print dents rate cut hopes; dollar firms",
    "Gold ETF outflows accelerate as investors take profits",
]
NEUTRAL = ["Apple launches new iPhone", "Gold steadies as traders await Fed decision"]


def test_lexicon_directions():
    for h in BULLISH:
        assert lexicon_score(h).impact > 0.3, h
    for h in BEARISH:
        assert lexicon_score(h).impact < -0.3, h
    for h in NEUTRAL:
        assert abs(lexicon_score(h).impact) < 0.35, h


def test_lexicon_subject_awareness():
    assert lexicon_score("Dollar falls sharply").impact > 0
    assert lexicon_score("Gold falls sharply").impact < 0
    assert lexicon_score("Yields rise to 5%").impact < 0


def _art(title, hours_ago, impact=None):
    a = Article(id=article_id(title), title=title, link="", source="t", summary="",
                published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago))
    if impact is not None:
        a.impact, a.confidence, a.theme = impact, 0.8, "fed"
    return a


def test_aggregate_decay_and_sign():
    fresh = [_art(f"bull {i}", 1, +2.0) for i in range(3)]
    stale = [_art(f"old bear {i}", 60, -2.0) for i in range(3)]
    ns = aggregate(fresh + stale, half_life_hours=10)
    assert ns.score > 40
    assert ns.n_bullish == 3 and ns.n_bearish == 3
    assert ns.drivers[0].impact == 2.0
    assert aggregate([]).score == 0


def test_apply_lexicon_fills_fields():
    arts = [_art(h, 1) for h in BULLISH]
    apply_lexicon(arts)
    assert all(a.impact is not None and a.scored_by == "lexicon" for a in arts)


def test_high_impact_detection():
    a = _art("FOMC decision: Fed holds rates steady", 1)
    assert is_high_impact(a, ["fomc"])
    b = _art("Something irrelevant", 1, impact=0.2)
    assert not is_high_impact(b, ["fomc"])


def test_title_normalisation_and_ids():
    assert normalize_title("Gold Rises 1% - Reuters") == normalize_title("gold rises 1%")
    assert article_id("Gold Rises 1% - Reuters") == article_id("Gold rises 1%")


def test_relevance_filter():
    assert is_relevant("Gold slips as dollar firms")
    assert not is_relevant("Local football team wins derby")


def test_parse_feed_rss():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test Feed</title>
    <item><title>Gold jumps as yields fall - Example News</title><link>https://x.test/1</link>
    <pubDate>Fri, 17 Oct 2026 12:00:00 GMT</pubDate><description>&lt;p&gt;Bullion gained.&lt;/p&gt;</description>
    <source url="https://example.news">Example News</source></item>
    <item><title>Celebrity bakes a cake</title><link>https://x.test/2</link><pubDate>Fri, 17 Oct 2026 12:00:00 GMT</pubDate></item>
    </channel></rss>"""
    arts = parse_feed(rss, "https://x.test/feed")
    assert len(arts) == 1
    a = arts[0]
    assert a.title == "Gold jumps as yields fall"
    assert a.source == "Example News"
    assert a.summary == "Bullion gained."
    assert a.published_at.tzinfo is not None


def test_extract_json_array_tolerates_fences():
    assert _extract_json_array('```json\n[{"id": "a", "impact": 1}]\n```')[0]["id"] == "a"
    assert _extract_json_array('Here you go: [{"id": "b"}] thanks')[0]["id"] == "b"


def _scored(title, impact, confidence, scored_by="claude"):
    from datetime import datetime, timezone
    from news.store import Article
    return Article(id="x", title=title, link="https://x.test", source="s", summary="", published_at=datetime.now(timezone.utc),
                   impact=impact, confidence=confidence, scored_by=scored_by)


def test_a_keyword_alone_is_not_high_impact_when_the_ai_reader_saw_no_gold_link():
    terms = ["tariff", "war", "rate hike", "ceasefire"]
    # scored 0 by the reader: a retailer's earnings and a feature article
    assert not is_high_impact(_scored("Target's turnaround gains traction, but tariff refunds mute the beat", 0.0, 0.7), terms)
    assert not is_high_impact(_scored("Inside the old gas pipeline where Ukraine fights an underground war", 0.0, 0.8), terms)
    assert not is_high_impact(_scored("Analysts ask whether a frozen war can last", 1.0, 0.3), terms)       # a guess, not a reading
    # a real reading from the reader, with the keyword
    assert is_high_impact(_scored("Fed's Waller says Fed should consider a half-point rate hike", -1.0, 0.6), terms)
    # strong score without any keyword still qualifies
    assert is_high_impact(_scored("US dollar at 19-month high", -2.0, 0.7), terms)
    # without the AI reader the keyword rule is unchanged
    assert is_high_impact(_scored("Tariff shock hits markets", 0.4, 0.5, scored_by="lexicon"), terms)


def test_repeats_of_one_story_are_recognised_but_a_new_development_is_not():
    from news.sentiment import same_story
    a = "Fed’s Waller Says Fed Should Consider Half-Point Rate Hike"
    assert same_story(a, "Waller Thinks Fed Should Consider Half-Point Rate Hike")
    assert same_story("Israeli tanks roll into central Gaza in first ground operation since ceasefire began",
                      "Israeli tanks roll into central Gaza in first known ground operation since ceasefire began")
    assert not same_story(a, "Fed hikes rates by 50 basis points")            # the decision itself is news
    assert not same_story(a, "Fed holds rates steady, signals cuts")
    assert not same_story(a, "Gold plunges to 14-month low as Fed rate hike bets intensify")
    assert not same_story("", a)
