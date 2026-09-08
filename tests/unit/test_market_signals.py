import pytest

from app.sources.market_data import MarketQuote
from app.sources.market_signals import (
    annualized_volatility_pct,
    extract_market_kpis,
    kpi_unit,
    max_drawdown_pct,
    period_change_pct,
)


def build_quote(**overrides) -> MarketQuote:
    defaults = {
        "symbol": "TCS.NS",
        "long_name": "Tata Consultancy Services Limited",
        "exchange_name": "NSE",
        "currency": "INR",
        "price": 2255.5,
        "previous_close": 2270.0,
        "day_high": 2274.5,
        "day_low": 2244.0,
        "fifty_two_week_high": 3350.0,
        "fifty_two_week_low": 1976.8,
        "volume": 2142654,
        "change_percent": -0.639,
        "period_start": None,
        "period_end": None,
        "closes": [100.0, 110.0, 99.0, 105.0],
        "raw_data": {},
    }
    defaults.update(overrides)
    return MarketQuote(**defaults)


def test_period_change_pct_uses_first_and_last_close():
    assert period_change_pct([100.0, 120.0, 110.0]) == pytest.approx(10.0)


def test_period_change_pct_needs_at_least_two_closes():
    assert period_change_pct([100.0]) is None


def test_max_drawdown_pct_measures_worst_peak_to_trough_decline():
    assert max_drawdown_pct([100.0, 110.0, 88.0, 95.0]) == pytest.approx(20.0)


def test_max_drawdown_pct_is_zero_for_a_monotonic_rise():
    assert max_drawdown_pct([100.0, 105.0, 110.0]) == pytest.approx(0.0)


def test_annualized_volatility_needs_at_least_two_returns():
    assert annualized_volatility_pct([100.0, 101.0]) is None


def test_annualized_volatility_is_positive_for_a_moving_series():
    volatility = annualized_volatility_pct([100.0, 105.0, 98.0, 103.0])

    assert volatility is not None
    assert volatility > 0


def test_extract_market_kpis_returns_expected_signals():
    kpis = extract_market_kpis(build_quote())

    assert kpis["market_price"] == pytest.approx(2255.5)
    assert kpis["market_drawdown_from_52w_high_pct"] == pytest.approx(32.6716, abs=1e-3)
    assert kpis["market_premium_over_52w_low_pct"] == pytest.approx(14.0985, abs=1e-3)
    assert kpis["market_price_change_pct_period"] == pytest.approx(5.0)
    assert kpis["market_max_drawdown_pct_period"] == pytest.approx(10.0)
    assert kpis["market_trading_volume"] == pytest.approx(2142654.0)


def test_extract_market_kpis_omits_signals_that_cannot_be_computed():
    kpis = extract_market_kpis(build_quote(closes=[], fifty_two_week_high=None, volume=None))

    assert "market_price_change_pct_period" not in kpis
    assert "market_drawdown_from_52w_high_pct" not in kpis
    assert "market_trading_volume" not in kpis
    assert kpis["market_price"] == pytest.approx(2255.5)


def test_kpi_unit_uses_quote_currency_for_price():
    assert kpi_unit("market_price", "INR") == "INR"
    assert kpi_unit("market_drawdown_from_52w_high_pct", "INR") == "percent"
