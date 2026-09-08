import pytest

from app.sources.market_data import (
    UnsupportedExchangeError,
    build_symbol,
    normalize_market_quote,
    parse_chart_payload,
)

CHART_PAYLOAD = {
    "chart": {
        "result": [
            {
                "meta": {
                    "symbol": "TCS.NS",
                    "longName": "Tata Consultancy Services Limited",
                    "fullExchangeName": "NSE",
                    "currency": "INR",
                    "regularMarketPrice": 2255.5,
                    "chartPreviousClose": 2270.0,
                    "regularMarketDayHigh": 2274.5,
                    "regularMarketDayLow": 2244.0,
                    "fiftyTwoWeekHigh": 3350.0,
                    "fiftyTwoWeekLow": 1976.8,
                    "regularMarketVolume": 2142654,
                    "regularMarketChangePercent": -0.639,
                },
                "timestamp": [1773000000, 1773086400],
                "indicators": {"quote": [{"close": [2270.0, None, 2255.5]}]},
            }
        ],
        "error": None,
    }
}


def test_build_symbol_adds_nse_suffix():
    assert build_symbol("TCS", "NSE") == "TCS.NS"


def test_build_symbol_adds_bse_suffix():
    assert build_symbol("tcs", "BSE") == "TCS.BO"


def test_build_symbol_leaves_us_tickers_bare():
    assert build_symbol("FRSH", "NASDAQ") == "FRSH"


def test_build_symbol_without_exchange_uses_bare_ticker():
    assert build_symbol("FRSH", None) == "FRSH"


def test_build_symbol_returns_none_without_ticker():
    assert build_symbol(None, "NSE") is None
    assert build_symbol("  ", "NSE") is None


def test_build_symbol_rejects_unmapped_exchange():
    with pytest.raises(UnsupportedExchangeError):
        build_symbol("ACME", "JSE")


def test_parse_chart_payload_extracts_meta_and_closes():
    quote = parse_chart_payload("TCS.NS", CHART_PAYLOAD)

    assert quote is not None
    assert quote.symbol == "TCS.NS"
    assert quote.long_name == "Tata Consultancy Services Limited"
    assert quote.exchange_name == "NSE"
    assert quote.currency == "INR"
    assert quote.price == pytest.approx(2255.5)
    assert quote.fifty_two_week_high == pytest.approx(3350.0)
    assert quote.volume == 2142654
    assert quote.closes == [2270.0, 2255.5]
    assert quote.period_start is not None
    assert quote.period_end is not None


def test_parse_chart_payload_returns_none_for_delisted_symbol():
    payload = {"chart": {"result": None, "error": {"code": "Not Found", "description": "symbol may be delisted"}}}

    assert parse_chart_payload("ZOMATO.NS", payload) is None


def test_normalize_market_quote_is_json_serializable():
    quote = parse_chart_payload("TCS.NS", CHART_PAYLOAD)
    normalized = normalize_market_quote(quote)

    assert normalized["symbol"] == "TCS.NS"
    assert normalized["observation_count"] == 2
    assert isinstance(normalized["period_end"], str)
