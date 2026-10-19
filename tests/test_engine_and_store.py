from datetime import datetime, timedelta, timezone

from bot import formatting as fmt
from market.analysis import analyze_technicals
from news.sentiment import aggregate
from news.store import Article, Store
from signals.engine import bias_changed, build_signal


def _news(score_articles):
    arts = []
    for i, imp in enumerate(score_articles):
        a = Article(id=f"a{i}", title=f"headline {i}", link="", source="t", summary="",
                    published_at=datetime.now(timezone.utc) - timedelta(hours=1))
        a.impact, a.confidence, a.theme = imp, 0.8, "fed"
        arts.append(a)
    return aggregate(arts)


def test_signal_levels_follow_direction(bull_market):
    tech = analyze_technicals(bull_market)
    news = _news([2.0, 2.0, 1.5, 1.0])
    sig = build_signal(tech, news, tech_weight=0.0, news_weight=1.0)
    assert sig.direction == "long"
    assert sig.stop < sig.price < sig.target1 < sig.target2

    news = _news([-2.0, -2.0, -1.5, -1.0])
    sig = build_signal(tech, news, tech_weight=0.0, news_weight=1.0)
    assert sig.direction == "short"
    assert sig.target2 < sig.target1 < sig.price < sig.stop


def test_signal_neutral_has_no_levels(flat_market):
    tech = analyze_technicals(flat_market)
    sig = build_signal(tech, _news([]), tech_weight=0.0, news_weight=1.0)
    assert sig.bias == "NEUTRAL" and sig.stop is None


def test_conflict_lowers_confidence(bull_market):
    tech = analyze_technicals(bull_market)
    tech.score = 60
    aligned = build_signal(tech, _news([2.0, 2.0, 2.0]))
    conflict = build_signal(tech, _news([-2.0, -2.0, -2.0]))
    assert aligned.agreement == "aligned" and conflict.agreement == "conflict"
    assert aligned.confidence > conflict.confidence


def test_bias_changed():
    class S:  # minimal stand-in
        bias = "BUY"
    assert bias_changed(None, S())
    assert bias_changed({"bias": "SELL"}, S())
    assert not bias_changed({"bias": "BUY"}, S())


def test_store_roundtrip(tmp_path):
    store = Store(tmp_path / "t.db")
    a = Article(id="x1", title="Gold rises", link="https://x", source="t", summary="",
                published_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert store.upsert_articles([a]) == 1
    assert store.upsert_articles([a]) == 0          # duplicate ignored
    assert store.has_article("x1")
    assert len(store.unscored_articles()) == 1
    a.impact, a.confidence, a.scored_by = 1.0, 0.7, "lexicon"
    store.update_scores([a])
    assert store.unscored_articles() == []
    got = store.recent_articles(hours=5)[0]
    assert got.impact == 1.0 and got.published_at.tzinfo is not None

    store.subscribe(42)
    assert store.subscribers() == [42] and store.alerts_enabled(42)
    store.set_alerts(42, False)
    assert store.subscribers(alerts_only=True) == []
    store.set_state("last_signal", {"bias": "BUY"})
    assert store.get_state("last_signal")["bias"] == "BUY"
    store.unsubscribe(42)
    assert store.subscribers() == []


def test_formatting_is_valid_html(bull_market):
    tech = analyze_technicals(bull_market)
    news = _news([1.0, -1.0])
    sig = build_signal(tech, news)
    text = fmt.format_full_report(tech, news, sig, "briefing <b>", bull_market.source, bull_market.last_bar_time)
    assert "&lt;b&gt;" in text               # user/AI text is escaped
    assert text.count("<b>") == text.count("</b>")
    assert len(text) < 4096 * 3
