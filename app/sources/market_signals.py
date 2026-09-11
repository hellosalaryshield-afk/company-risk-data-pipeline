import math

from app.sources.market_data import MarketQuote

TRADING_DAYS_PER_YEAR = 252


def daily_log_returns(closes: list[float]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(closes, closes[1:], strict=False):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    return returns


def annualized_volatility_pct(
    closes: list[float],
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Annualised standard deviation of log returns.

    `periods_per_year` must match the sampling interval: 252 for daily bars, 52 for
    weekly, 12 for monthly. Using the daily constant on monthly data overstates
    volatility by roughly 4.5x.
    """
    returns = daily_log_returns(closes)
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year) * 100


def period_change_pct(closes: list[float]) -> float | None:
    if len(closes) < 2 or closes[0] <= 0:
        return None
    return (closes[-1] - closes[0]) / closes[0] * 100


def max_drawdown_pct(closes: list[float]) -> float | None:
    """Largest peak-to-trough decline over the window, as a positive percentage."""
    if len(closes) < 2:
        return None

    peak = closes[0]
    worst = 0.0
    for close in closes:
        if close > peak:
            peak = close
        if peak > 0:
            drawdown = (peak - close) / peak * 100
            worst = max(worst, drawdown)
    return worst


def drawdown_from_high_pct(price: float | None, high: float | None) -> float | None:
    if price is None or high is None or high <= 0:
        return None
    return (high - price) / high * 100


def premium_over_low_pct(price: float | None, low: float | None) -> float | None:
    if price is None or low is None or low <= 0:
        return None
    return (price - low) / low * 100


def extract_market_kpis(quote: MarketQuote) -> dict[str, float]:
    """Derive market-risk KPIs from one price history window.

    Higher drawdown and higher volatility both indicate elevated distress risk, so the
    signs are kept consistent: every *_drawdown_pct value is positive when the price is
    below its reference level.
    """
    candidates = {
        "market_price": quote.price,
        "market_price_change_pct_period": period_change_pct(quote.closes),
        "market_drawdown_from_52w_high_pct": drawdown_from_high_pct(quote.price, quote.fifty_two_week_high),
        "market_premium_over_52w_low_pct": premium_over_low_pct(quote.price, quote.fifty_two_week_low),
        "market_max_drawdown_pct_period": max_drawdown_pct(quote.closes),
        "market_annualized_volatility_pct": annualized_volatility_pct(quote.closes),
        "market_trading_volume": float(quote.volume) if quote.volume is not None else None,
    }
    return {name: round(value, 4) for name, value in candidates.items() if value is not None}


KPI_UNITS = {
    "market_price": "currency",
    "market_price_change_pct_period": "percent",
    "market_drawdown_from_52w_high_pct": "percent",
    "market_premium_over_52w_low_pct": "percent",
    "market_max_drawdown_pct_period": "percent",
    "market_annualized_volatility_pct": "percent",
    "market_trading_volume": "shares",
}


def kpi_unit(kpi_name: str, currency: str | None) -> str:
    unit = KPI_UNITS.get(kpi_name, "value")
    if unit == "currency":
        return currency or "currency"
    return unit
