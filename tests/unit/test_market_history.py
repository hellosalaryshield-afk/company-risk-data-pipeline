from datetime import UTC, datetime

import pytest

from app.models.collection import KpiObservation
from app.pipeline.market_history import (
    backfill_company,
    backfill_registry,
    existing_history_dates,
    kpis_at_index,
)
from app.sources.market_data import parse_dated_closes
from app.sources.market_signals import annualized_volatility_pct


def monthly_points(values: list[float], start_year: int = 2024) -> list[tuple[datetime, float]]:
    return [
        (datetime(start_year + (i // 12), (i % 12) + 1, 1, tzinfo=UTC), value)
        for i, value in enumerate(values)
    ]


def test_parse_dated_closes_pairs_timestamps_with_prices():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1704067200, 1706745600],
                    "indicators": {"quote": [{"close": [100.0, 110.0]}]},
                }
            ]
        }
    }

    points = parse_dated_closes(payload)

    assert len(points) == 2
    assert points[0][1] == pytest.approx(100.0)
    assert points[0][0].year == 2024


def test_parse_dated_closes_drops_a_point_missing_either_half():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1704067200, 1706745600, 1709251200],
                    "indicators": {"quote": [{"close": [100.0, None, 120.0]}]},
                }
            ]
        }
    }

    assert [value for _, value in parse_dated_closes(payload)] == [100.0, 120.0]


def test_parse_dated_closes_handles_an_empty_payload():
    assert parse_dated_closes({"chart": {"result": None}}) == []


def test_volatility_scales_with_the_sampling_interval():
    """Using the daily constant on monthly bars overstates volatility badly."""
    closes = [100, 105, 98, 110, 103]

    daily = annualized_volatility_pct(closes)
    monthly = annualized_volatility_pct(closes, periods_per_year=12)

    assert daily > monthly
    assert daily / monthly == pytest.approx((252 / 12) ** 0.5, rel=1e-6)


def test_kpis_at_index_uses_only_data_up_to_that_point():
    """The whole point: a 2024 figure must not contain 2025 prices."""
    points = monthly_points([100, 120, 90, 200, 50])

    at_index_1 = kpis_at_index(points, 1, lookback=12, periods_per_year=12)

    # At index 1 the series is [100, 120]: 120 is the high, so no drawdown yet.
    assert at_index_1["market_price"] == pytest.approx(120)
    assert at_index_1["market_drawdown_from_52w_high_pct"] == pytest.approx(0.0)


def test_kpis_at_index_sees_a_later_drawdown():
    points = monthly_points([100, 120, 90])

    at_index_2 = kpis_at_index(points, 2, lookback=12, periods_per_year=12)

    # Series is [100, 120, 90]: high 120, now 90, so 25% below the high.
    assert at_index_2["market_price"] == pytest.approx(90)
    assert at_index_2["market_drawdown_from_52w_high_pct"] == pytest.approx(25.0)


def test_kpis_at_index_respects_the_trailing_window():
    """An old peak outside the lookback must not count as the 52-week high."""
    points = monthly_points([1000, 100, 100, 100, 110])

    windowed = kpis_at_index(points, 4, lookback=3, periods_per_year=12)

    # Window is the last 3 points [100, 100, 110], so 110 is the high, not 1000.
    assert windowed["market_drawdown_from_52w_high_pct"] == pytest.approx(0.0)


def test_kpis_at_index_needs_at_least_two_points():
    assert kpis_at_index(monthly_points([100]), 0, lookback=12, periods_per_year=12) == {}


def test_backfill_writes_one_observation_set_per_historical_date(db_session):
    company = db_session.create_company(
        canonical_name="Tata Consultancy Services", aliases=["TCS"], ticker="TCS", exchange="NSE"
    )
    points = monthly_points([100, 110, 105, 120])

    result = backfill_company(db_session.session, company, points=points)

    assert result["status"] == "completed"
    # The first point has no prior bar to compare against, so it yields nothing.
    assert result["observations_written"] == 3
    assert result["symbol"] == "TCS.NS"

    prices = (
        db_session.session.query(KpiObservation)
        .filter_by(company_id=company.id, kpi_name="market_price")
        .all()
    )
    assert len(prices) == 3
    assert {p.published_at.year for p in prices} == {2024}


def test_backfilled_observations_are_dated_and_carry_their_window(db_session):
    """Without a per-point date and window the history is useless for backtesting."""
    company = db_session.create_company(
        canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE"
    )

    backfill_company(db_session.session, company, points=monthly_points([100, 110, 120]))

    observation = (
        db_session.session.query(KpiObservation)
        .filter_by(company_id=company.id, kpi_name="market_price")
        .order_by(KpiObservation.published_at)
        .first()
    )
    assert observation.published_at is not None
    assert observation.period_end == observation.published_at
    assert observation.period_start <= observation.period_end
    assert observation.extra_metadata["backfill"] is True


def test_backfill_is_idempotent(db_session):
    company = db_session.create_company(
        canonical_name="Infosys", aliases=["INFY"], ticker="INFY", exchange="NSE"
    )
    points = monthly_points([100, 110, 120])

    first = backfill_company(db_session.session, company, points=points)
    second = backfill_company(db_session.session, company, points=points)

    assert first["observations_written"] == 2
    assert second["observations_written"] == 0
    # Only the two dates that were actually written are skipped. The first point yields no
    # KPIs at all, because there is no prior bar to compare it against.
    assert second["observations_skipped"] == 2


def test_existing_history_dates_reports_what_is_already_stored(db_session):
    company = db_session.create_company(
        canonical_name="Wipro", aliases=["WIPRO Ltd"], ticker="WIPRO", exchange="NSE"
    )
    backfill_company(db_session.session, company, points=monthly_points([100, 110]))

    assert len(existing_history_dates(db_session.session, company.id)) == 1


def test_unlisted_and_unsupported_exchanges_are_skipped(db_session):
    unlisted = db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"])
    odd = db_session.create_company(
        canonical_name="Odd Listing", aliases=["Odd Ltd"], ticker="ODD", exchange="MOON"
    )

    assert backfill_company(db_session.session, unlisted)["status"] == "not_applicable"
    assert backfill_company(db_session.session, odd)["status"] == "not_applicable"


def test_backfill_registry_only_touches_listed_companies(db_session, monkeypatch):
    db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")
    db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"])

    monkeypatch.setattr(
        "app.pipeline.market_history.fetch_dated_history",
        lambda symbol, range_period, interval, timeout=30.0: monthly_points([100, 110, 120]),
    )

    result = backfill_registry(db_session.session)

    assert result["companies"] == 1
    assert result["observations_written"] == 2


def test_infer_interval_reads_the_spacing_not_the_request():
    """Yahoo ignores the requested interval when the history is short."""
    from datetime import timedelta

    from app.pipeline.market_history import infer_interval

    def spaced(days: int, count: int):
        base = datetime(2024, 1, 1, tzinfo=UTC)
        return [(base + timedelta(days=days * i), 100.0 + i) for i in range(count)]

    assert infer_interval(spaced(1, 40)) == "1d"
    assert infer_interval(spaced(7, 40)) == "1wk"
    assert infer_interval(spaced(31, 40)) == "1mo"
    assert infer_interval(spaced(1, 2)) == "1mo"
    assert infer_interval([]) == "1mo"


def test_daily_bars_are_not_annualised_as_if_monthly(db_session):
    """The bug this guards: a 2024 listing gets daily bars from a max/1mo request.

    Annualising those with 12 periods understated volatility by sqrt(252/12), about 4.6x.
    """
    from datetime import timedelta

    company = db_session.create_company(
        canonical_name="Swiggy", aliases=["Bundl"], ticker="SWIGGY", exchange="NSE"
    )
    base = datetime(2024, 11, 1, tzinfo=UTC)
    prices = [100.0, 104.0, 99.0, 107.0, 102.0, 110.0, 105.0, 112.0]
    daily_points = [(base + timedelta(days=i), p) for i, p in enumerate(prices)]

    result = backfill_company(db_session.session, company, points=daily_points, interval="1mo")

    assert result["interval"] == "1d"
    observation = (
        db_session.session.query(KpiObservation)
        .filter_by(company_id=company.id, kpi_name="market_annualized_volatility_pct")
        .order_by(KpiObservation.published_at.desc())
        .first()
    )
    assert observation.extra_metadata["interval"] == "1d"
    assert observation.extra_metadata["requested_interval"] == "1mo"
    # Annualised on 252 periods, this series is highly volatile. The monthly constant
    # would have produced something under 20%.
    assert float(observation.value_numeric) > 50
