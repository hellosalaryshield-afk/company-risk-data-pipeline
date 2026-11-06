"""The scorecard arithmetic, including the eight tests required by section 9 of
"Layoff Risk Score: Developer Instructions (Version 1)"."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.scoring.config import FeatureSpec, feature_specs, load_config
from app.scoring.engine import (
    NewsEvent,
    band_for,
    confidence_for,
    drivers,
    feature_score,
    news_score,
    overall_score,
    percentile_risk,
    pillar_score,
    top_drivers,
)

CONFIG = load_config()
WEIGHTS = CONFIG["pillar_weights"]
TODAY = datetime(2026, 11, 6, tzinfo=UTC)


def full(score):
    return (score, 1.0)


# Section 9, test 1
def test_worked_example_1_all_data_present_gives_62():
    pillars = {"financial": full(50), "sentiment": full(72), "news": full(78), "profile": full(35)}

    result = overall_score(pillars, WEIGHTS, sector_average=41, config=CONFIG)

    assert result.raw == pytest.approx(62.0)
    assert result.coverage == pytest.approx(1.0)
    assert result.final == pytest.approx(62.0)  # full coverage, so no pull toward the sector


# Section 9, test 2
def test_worked_example_2_no_financial_data_gives_65_then_63():
    pillars = {"financial": (None, 0.0), "sentiment": full(72), "news": full(78), "profile": full(35)}

    result = overall_score(pillars, WEIGHTS, sector_average=41, config=CONFIG)

    assert result.raw == pytest.approx(65.0)
    assert result.coverage == pytest.approx(0.80)
    assert result.final == pytest.approx(62.6)
    assert round(result.final) == 63


# Section 9, test 3
def test_direction_check():
    peers = [2.8, 3.0, 3.3, 3.6, 3.9, 4.1]
    rating = FeatureSpec("glassdoor_rating", "Rating", "sentiment", 0.25, higher_is_safer=True)
    debt = FeatureSpec("debt_to_equity", "Debt", "financial", 0.15, higher_is_safer=False)

    rating_scores = [feature_score(rating, v, peers, 5) for v in (2.5, 3.0, 3.5, 4.0, 4.5)]
    debt_scores = [feature_score(debt, v, peers, 5) for v in (2.5, 3.0, 3.5, 4.0, 4.5)]

    assert rating_scores == sorted(rating_scores, reverse=True)  # safer as rating rises
    assert debt_scores == sorted(debt_scores)  # riskier as debt rises


# Section 9, test 4
def test_drivers_add_up_to_the_score_before_adjustment():
    specs = {s.key: s for s in feature_specs(CONFIG, listed=True)}
    features = [
        (specs["share_price_change_6m"], 80.0, 50.0, "Share price"),
        (specs["glassdoor_rating"], 70.0, 45.0, "Rating"),
        (specs["ceo_approval_share"], 30.0, 50.0, "CEO"),
        (specs["company_age_years"], 20.0, 55.0, "Age"),
    ]
    available = {"financial": 0.15, "sentiment": 0.40, "profile": 0.30}
    events = news_score(
        [
            NewsEvent("a", "leadership_exit", TODAY - timedelta(days=10), "CPO exits"),
            NewsEvent("b", "funding_raised", TODAY - timedelta(days=20), "Raised funding"),
        ],
        TODAY,
        CONFIG,
    )[1]
    news_value = sum(e.decayed_points for e in events)
    pillars = {
        "financial": pillar_score([(0.15, 80.0)]),
        "sentiment": pillar_score([(0.25, 70.0), (0.15, 30.0)]),
        "profile": pillar_score([(0.30, 20.0)]),
        "news": (news_value, 1.0),
    }
    overall = overall_score(pillars, WEIGHTS, sector_average=41, config=CONFIG)

    baseline, items = drivers(features, available, overall.effective_weights, (news_value, 12.0, events))

    assert baseline + sum(d.points for d in items) == pytest.approx(overall.raw, abs=0.01)


# Section 9, test 5
def test_score_stays_between_0_and_100_whatever_the_inputs():
    rng = random.Random(7)
    for _ in range(500):
        pillars = {
            name: (rng.uniform(-50, 150) if rng.random() > 0.2 else None, rng.random())
            for name in ("financial", "sentiment", "news", "profile")
        }
        result = overall_score(pillars, WEIGHTS, sector_average=rng.uniform(0, 100), config=CONFIG)
        if result.final is not None:
            assert 0 <= result.final <= 100


# Section 9, test 6
def test_almost_no_data_returns_not_enough_data():
    pillars = {"financial": (40.0, 0.15), "sentiment": (None, 0.0), "news": (None, 0.0), "profile": (30.0, 0.30)}

    result = overall_score(pillars, WEIGHTS, sector_average=41, config=CONFIG)

    assert result.coverage < 0.30
    assert result.final is None and result.raw is None


# Section 9, test 7
def test_news_decay_halves_an_event_after_45_days():
    today_score, _ = news_score([NewsEvent("x", "layoffs", TODAY, "Layoffs")], TODAY, CONFIG)
    old_score, _ = news_score([NewsEvent("x", "layoffs", TODAY - timedelta(days=45), "Layoffs")], TODAY, CONFIG)

    assert today_score == pytest.approx(40.0)
    assert old_score == pytest.approx(20.0)


# Section 9, test 8 (engine side; the article grouping is tested in test_scoring_service.py)
def test_the_same_type_within_7_days_counts_once():
    events = [NewsEvent(str(i), "layoffs", TODAY - timedelta(days=i), "Layoffs") for i in (0, 2, 5)]

    score, kept = news_score(events, TODAY, CONFIG)

    assert len(kept) == 1
    assert score == pytest.approx(40.0)


def test_spec_percentile_example():
    peers = [2.9, 3.1, 3.3] + [3.6] * 7  # 30% at or below 3.3

    assert percentile_risk(3.3, peers, higher_is_safer=True) == pytest.approx(70.0)


def test_at_most_three_events_of_one_type_count():
    events = [NewsEvent(str(i), "leadership_exit", TODAY - timedelta(days=i * 10), "Exit") for i in range(5)]

    _, kept = news_score(events, TODAY, CONFIG)

    assert len(kept) == 3


def test_news_score_is_clamped_and_positive_news_cannot_go_below_zero():
    # 40 + 50 + 20 = 110 points today, so the score is clamped to 100.
    bad = [NewsEvent(str(i), t, TODAY, "x") for i, t in enumerate(["layoffs", "salary_delay", "funding_trouble"])]
    good = [NewsEvent("g", "funding_raised", TODAY, "Raised")]

    assert news_score(bad, TODAY, CONFIG)[0] == 100.0
    assert news_score(good, TODAY, CONFIG)[0] == 0.0


def test_unscored_types_are_ignored():
    # "growth" is a retired category that older stored records may still carry.
    score, kept = news_score([NewsEvent("n", "neutral", TODAY, "x"), NewsEvent("g", "growth", TODAY, "x")], TODAY, CONFIG)

    assert score == 0.0 and kept == []


def test_severity_scaling_is_off_by_default_and_applies_when_configured():
    event = [NewsEvent("x", "layoffs", TODAY, "Layoffs", severity=3)]
    scaled = {**CONFIG, "severity_multipliers": {"1": 0.5, "2": 1.0, "3": 1.5}}

    assert news_score(event, TODAY, CONFIG)[0] == pytest.approx(40.0)
    assert news_score(event, TODAY, scaled)[0] == pytest.approx(60.0)


def test_pillar_coverage_example_from_the_spec():
    # Sentiment with everything except CEO approval (weight 0.15).
    score, coverage = pillar_score([(0.25, 60), (0.25, 70), (0.15, None), (0.15, 50), (0.20, 80)])

    assert coverage == pytest.approx(0.85)
    assert score == pytest.approx((0.25 * 60 + 0.25 * 70 + 0.15 * 50 + 0.20 * 80) / 0.85)


def test_binary_features_score_directly():
    overdue = FeatureSpec("filings_overdue", "Filings overdue", "profile", 0.30, higher_is_safer=False, binary=True)

    assert feature_score(overdue, 1.0, [], 5) == 100.0
    assert feature_score(overdue, 0.0, [], 5) == 0.0


def test_too_few_peer_values_counts_as_missing():
    rating = FeatureSpec("glassdoor_rating", "Rating", "sentiment", 0.25, higher_is_safer=True)

    assert feature_score(rating, 3.5, [3.5], min_values=5) is None
    assert feature_score(rating, None, [3, 4, 5, 3, 4], min_values=5) is None


@pytest.mark.parametrize(
    ("score", "band"), [(0, "Low"), (25, "Low"), (25.4, "Low"), (26, "Moderate"), (50, "Moderate"), (51, "Elevated"), (76, "High"), (100, "High")]
)
def test_bands(score, band):
    assert band_for(score, CONFIG) == band


@pytest.mark.parametrize(("coverage", "level"), [(0.95, "High"), (0.80, "High"), (0.79, "Medium"), (0.50, "Medium"), (0.49, "Limited data")])
def test_confidence(coverage, level):
    assert confidence_for(coverage, CONFIG) == level


def test_top_drivers_keeps_four_up_and_two_down():
    from app.scoring.engine import Driver

    items = [Driver(str(p), "news", p) for p in (9, 1, 7, 5, 3, -2, -6, -1, 0)]

    chosen = [d.points for d in top_drivers(items, 4, 2)]

    assert chosen == [9, 7, 5, 3, -6, -2]


def test_good_news_never_shows_as_raising_risk_when_news_is_mixed():
    events = news_score(
        [
            NewsEvent("bad", "regulatory_legal", TODAY - timedelta(days=5), "Licence suspended"),
            NewsEvent("good", "hiring_surge", TODAY - timedelta(days=2), "Hiring 3,000 people"),
        ],
        TODAY,
        CONFIG,
    )[1]
    news_value = max(sum(e.decayed_points for e in events), 0)
    effective = {"news": 0.30, "profile": 0.06}

    baseline, items = drivers([], {}, effective, (news_value, 3.5, events))

    by_label = {d.label: d.points for d in items}
    assert by_label["Licence suspended"] > 0
    assert by_label["Hiring 3,000 people"] < 0
    expected = (0.30 / 0.36) * news_value
    assert baseline + sum(by_label.values()) == pytest.approx(expected, abs=0.01)
