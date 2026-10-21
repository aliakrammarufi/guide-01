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
    # levels are planned around the entry zone (a pullback), not the current price
    assert sig.stop < sig.zone_lo < sig.zone_hi <= sig.price + 0.05 * sig.atr + 0.02
    assert sig.zone_mid < sig.target1 < sig.target2 and sig.chase_limit > sig.price

    news = _news([-2.0, -2.0, -1.5, -1.0])
    sig = build_signal(tech, news, tech_weight=0.0, news_weight=1.0)
    assert sig.direction == "short"
    assert sig.price - 0.05 * sig.atr - 0.02 <= sig.zone_lo < sig.zone_hi < sig.stop
    assert sig.target2 < sig.target1 < sig.zone_mid and sig.chase_limit < sig.price


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


def test_entry_plan_invariants_across_markets():
    """Zone on the right side of price, stop beyond the zone, targets beyond the zone middle, chase level beyond price."""
    from market.synthetic import make_market
    from signals.engine import plan_entry

    for seed in range(1, 13):
        for drift in (-0.02, 0.0, 0.02):
            t = analyze_technicals(make_market(drift_h=drift, seed=seed, bars=1200, start=4000.0))
            atr, p = t.atr, t.price
            for direction in ("long", "short"):
                pl = plan_entry(direction, t)
                lo, hi, st, t1, t2, ch = pl["zone_lo"], pl["zone_hi"], pl["stop"], pl["target1"], pl["target2"], pl["chase_limit"]
                mid = (lo + hi) / 2
                assert lo < hi and 0.25 * atr - 0.02 <= hi - lo <= 1.6 * atr + 0.02
                if direction == "long":
                    assert hi <= p + 0.05 * atr + 0.02 and st < lo and t1 > mid and t2 > t1 and ch > hi and ch > p
                    assert mid - st <= 1.5 * atr + 0.02
                else:
                    assert lo >= p - 0.05 * atr - 0.02 and st > hi and t1 < mid and t2 < t1 and ch < lo and ch < p
                    assert st - mid <= 1.5 * atr + 0.02


def test_lexicon_scored_recent_only_returns_keyword_scored(tmp_path):
    store = Store(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    rows = [Article(id=f"k{i}", title=f"headline {i}", link="", source="t", summary="", published_at=now - timedelta(hours=i))
            for i in range(4)]
    store.upsert_articles(rows)
    rows[0].impact, rows[0].scored_by = 0.5, "lexicon"
    rows[1].impact, rows[1].scored_by = -0.5, "claude"
    rows[2].impact, rows[2].scored_by = 0.0, None          # scored, source unknown → treat as keyword
    store.update_scores(rows[:3])                           # rows[3] stays unscored
    got = store.lexicon_scored_recent(hours=36)
    assert sorted(a.id for a in got) == ["k0", "k2"]
    assert [a.id for a in store.lexicon_scored_recent(hours=0.5)] == ["k0"]
    rows[0].scored_by = "claude"
    store.update_scores([rows[0]])
    assert [a.id for a in store.lexicon_scored_recent(hours=36)] == ["k2"]
