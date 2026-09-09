from datetime import UTC, datetime

import pytest

from app.sources.gdelt import (
    GdeltFetchResult,
    GdeltTimeline,
    parse_gdelt_date,
    parse_timeline,
)
from app.sources.gdelt_signals import (
    extract_gdelt_kpis,
    negative_day_pct,
    spike_ratio,
    split_half_change,
    timeline_stats,
)

# Shape copied from a real GDELT timelinetone response on 2026-09-09.
TONE_PAYLOAD = {
    "query_details": {"title": '"Tata Consultancy Services"', "date_resolution": "day"},
    "timeline": [
        {
            "series": "Average Tone",
            "data": [
                {"date": "20260612T000000Z", "value": 0.5981},
                {"date": "20260613T000000Z", "value": -2.5441},
                {"date": "20260614T000000Z", "value": 0.9219},
                {"date": "20260615T000000Z", "value": -1.7129},
            ],
        }
    ],
}


def test_parse_gdelt_date_reads_the_compact_stamp():
    assert parse_gdelt_date("20260612T000000Z") == datetime(2026, 6, 12, tzinfo=UTC)


def test_parse_gdelt_date_rejects_junk():
    assert parse_gdelt_date("not-a-date") is None
    assert parse_gdelt_date(None) is None


def test_parse_timeline_reads_dates_and_values():
    timeline = parse_timeline(TONE_PAYLOAD)

    assert timeline is not None
    assert timeline.series == "Average Tone"
    assert len(timeline.values) == 4
    assert timeline.values[1] == pytest.approx(-2.5441)
    assert timeline.dates[0] == datetime(2026, 6, 12, tzinfo=UTC)


def test_parse_timeline_returns_none_for_an_empty_series():
    assert parse_timeline({"timeline": []}) is None
    assert parse_timeline({"timeline": [{"series": "Average Tone", "data": []}]}) is None


def test_parse_timeline_skips_points_missing_a_date_or_value():
    payload = {
        "timeline": [
            {
                "series": "Average Tone",
                "data": [
                    {"date": "20260612T000000Z", "value": 1.0},
                    {"date": None, "value": 2.0},
                    {"date": "20260614T000000Z", "value": None},
                ],
            }
        ]
    }

    timeline = parse_timeline(payload)

    assert timeline is not None
    assert timeline.values == [1.0]


def test_negative_day_pct_counts_negative_days():
    assert negative_day_pct([1.0, -1.0, -2.0, 3.0]) == pytest.approx(50.0)
    assert negative_day_pct([]) is None


def test_split_half_change_is_positive_when_tone_worsens():
    # Earlier half averages 2.0, recent half averages -2.0, so tone declined by 4.0.
    assert split_half_change([2.0, 2.0, -2.0, -2.0]) == pytest.approx(4.0)


def test_split_half_change_is_negative_when_tone_improves():
    assert split_half_change([-2.0, -2.0, 2.0, 2.0]) == pytest.approx(-4.0)


def test_split_half_change_needs_enough_points():
    assert split_half_change([1.0, 2.0]) is None


def test_spike_ratio_flags_a_burst_of_coverage():
    assert spike_ratio([1.0, 1.0, 1.0, 5.0]) == pytest.approx(2.5)
    assert spike_ratio([]) is None
    assert spike_ratio([0.0, 0.0]) is None


def test_timeline_stats_summarizes_a_series():
    stats = timeline_stats(GdeltTimeline("Average Tone", [], [1.0, -3.0, 2.0]))

    assert stats["min"] == pytest.approx(-3.0)
    assert stats["max"] == pytest.approx(2.0)
    assert stats["points"] == 3


def test_timeline_stats_handles_a_missing_series():
    assert timeline_stats(None) == {}


def test_extract_gdelt_kpis_builds_tone_and_volume_signals():
    result = GdeltFetchResult(
        company_name="Tata Consultancy Services",
        timespan="3m",
        tone=GdeltTimeline("Average Tone", [], [2.0, 2.0, -2.0, -2.0]),
        volume=GdeltTimeline("Volume Intensity", [], [0.01, 0.01, 0.01, 0.05]),
        articles=[{"title": "a"}, {"title": "b"}],
    )

    kpis = extract_gdelt_kpis(result)

    assert kpis["gdelt_avg_tone"] == pytest.approx(0.0)
    assert kpis["gdelt_worst_day_tone"] == pytest.approx(-2.0)
    assert kpis["gdelt_negative_day_pct"] == pytest.approx(50.0)
    assert kpis["gdelt_tone_decline"] == pytest.approx(4.0)
    assert kpis["gdelt_volume_spike_ratio"] == pytest.approx(2.5)
    assert kpis["gdelt_article_count"] == pytest.approx(2.0)


def test_extract_gdelt_kpis_omits_signals_that_were_not_returned():
    """A partial fetch must not invent a zero for a missing series."""
    result = GdeltFetchResult(
        company_name="Razorpay",
        timespan="3m",
        tone=None,
        volume=GdeltTimeline("Volume Intensity", [], [0.01, 0.02]),
        articles=[],
    )

    kpis = extract_gdelt_kpis(result)

    assert "gdelt_avg_tone" not in kpis
    assert "gdelt_negative_day_pct" not in kpis
    assert "gdelt_article_count" not in kpis
    assert kpis["gdelt_volume_mean_pct"] == pytest.approx(0.015)
