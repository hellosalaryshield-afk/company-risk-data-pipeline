from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

YAHOO_CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo appends a market suffix to non-US tickers. Bare symbols are US-listed.
EXCHANGE_SUFFIXES = {
    "NSE": ".NS",
    "NSI": ".NS",
    "BSE": ".BO",
    "BOM": ".BO",
    "NASDAQ": "",
    "NASDAQGS": "",
    "NYSE": "",
    "NYSEAMERICAN": "",
    "LSE": ".L",
    "TSX": ".TO",
    "ASX": ".AX",
    "SGX": ".SI",
}

# Yahoo rejects requests without a browser-style User-Agent.
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CompanyRiskDataPipeline/1.0)"}


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    long_name: str | None
    exchange_name: str | None
    currency: str | None
    price: float | None
    previous_close: float | None
    day_high: float | None
    day_low: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    volume: int | None
    change_percent: float | None
    period_start: datetime | None
    period_end: datetime | None
    closes: list[float]
    raw_data: dict[str, Any]


class UnsupportedExchangeError(ValueError):
    pass


def build_symbol(ticker: str | None, exchange: str | None) -> str | None:
    """Map a stored ticker/exchange pair onto a Yahoo Finance symbol."""
    clean_ticker = (ticker or "").strip().upper()
    if not clean_ticker:
        return None

    clean_exchange = (exchange or "").strip().upper().replace(" ", "")
    if not clean_exchange:
        return clean_ticker

    if clean_exchange not in EXCHANGE_SUFFIXES:
        raise UnsupportedExchangeError(f"No Yahoo Finance symbol suffix is mapped for exchange {exchange}.")

    return f"{clean_ticker}{EXCHANGE_SUFFIXES[clean_exchange]}"


class YahooMarketDataClient:
    """Keyless client for the Yahoo Finance public chart endpoint.

    Only the v8 chart endpoint is used. The v10 quoteSummary endpoint now requires a
    crumb/cookie pair and returns 401 Invalid Crumb, so it is deliberately not used.
    """

    def __init__(self, timeout_seconds: float = 12.0):
        self.timeout_seconds = timeout_seconds

    def fetch_price_history(self, symbol: str, range_period: str = "6mo", interval: str = "1d") -> MarketQuote | None:
        response = httpx.get(
            YAHOO_CHART_ENDPOINT.format(symbol=symbol),
            params={"range": range_period, "interval": interval},
            headers=REQUEST_HEADERS,
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return None

        response.raise_for_status()
        payload = response.json()
        return parse_chart_payload(symbol, payload)


def parse_chart_payload(symbol: str, payload: dict[str, Any]) -> MarketQuote | None:
    chart = payload.get("chart") or {}
    results = chart.get("result")
    if not results:
        return None

    result = results[0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quote_blocks = ((result.get("indicators") or {}).get("quote")) or [{}]
    closes = [value for value in (quote_blocks[0].get("close") or []) if value is not None]

    return MarketQuote(
        symbol=meta.get("symbol") or symbol,
        long_name=meta.get("longName") or meta.get("shortName"),
        exchange_name=meta.get("fullExchangeName") or meta.get("exchangeName"),
        currency=meta.get("currency"),
        price=to_float(meta.get("regularMarketPrice")),
        previous_close=to_float(meta.get("chartPreviousClose")),
        day_high=to_float(meta.get("regularMarketDayHigh")),
        day_low=to_float(meta.get("regularMarketDayLow")),
        fifty_two_week_high=to_float(meta.get("fiftyTwoWeekHigh")),
        fifty_two_week_low=to_float(meta.get("fiftyTwoWeekLow")),
        volume=to_int(meta.get("regularMarketVolume")),
        change_percent=to_float(meta.get("regularMarketChangePercent")),
        period_start=to_datetime(timestamps[0]) if timestamps else None,
        period_end=to_datetime(timestamps[-1]) if timestamps else None,
        closes=closes,
        raw_data={"meta": meta, "close_count": len(closes)},
    )


def normalize_market_quote(quote: MarketQuote) -> dict[str, Any]:
    return {
        "symbol": quote.symbol,
        "long_name": quote.long_name,
        "exchange_name": quote.exchange_name,
        "currency": quote.currency,
        "price": quote.price,
        "previous_close": quote.previous_close,
        "day_high": quote.day_high,
        "day_low": quote.day_low,
        "fifty_two_week_high": quote.fifty_two_week_high,
        "fifty_two_week_low": quote.fifty_two_week_low,
        "volume": quote.volume,
        "change_percent": quote.change_percent,
        "period_start": quote.period_start.isoformat() if quote.period_start else None,
        "period_end": quote.period_end.isoformat() if quote.period_end else None,
        "observation_count": len(quote.closes),
    }


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def parse_dated_closes(payload: dict[str, Any]) -> list[tuple[datetime, float]]:
    """Closing prices paired with their dates, oldest first.

    `parse_chart_payload` drops the timestamps because a current quote does not need them.
    Historical backfill does: without a date per point there is no way to say what was known
    when, and the whole series becomes unusable for a point-in-time feature.
    """
    chart = payload.get("chart") or {}
    results = chart.get("result")
    if not results:
        return []

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_blocks = ((result.get("indicators") or {}).get("quote")) or [{}]
    closes = (quote_blocks[0].get("close")) or []

    points: list[tuple[datetime, float]] = []
    for timestamp, close in zip(timestamps, closes):
        if timestamp is None or close is None:
            continue
        points.append((datetime.fromtimestamp(timestamp, tz=UTC), float(close)))

    return points
