import httpx
from sqlalchemy import select

from app.models.collection import KpiObservation, SourceRecord
from app.pipeline import market_collection
from app.pipeline.market_collection import collect_market_for_company
from app.sources.market_data import MarketQuote


def build_quote() -> MarketQuote:
    return MarketQuote(
        symbol="TCS.NS",
        long_name="Tata Consultancy Services Limited",
        exchange_name="NSE",
        currency="INR",
        price=2255.5,
        previous_close=2270.0,
        day_high=2274.5,
        day_low=2244.0,
        fifty_two_week_high=3350.0,
        fifty_two_week_low=1976.8,
        volume=2142654,
        change_percent=-0.639,
        period_start=None,
        period_end=None,
        closes=[2500.0, 2400.0, 2255.5],
        raw_data={"meta": {"symbol": "TCS.NS"}},
    )


def test_collect_market_stores_source_record_and_kpis(db_session):
    company = db_session.create_company(
        canonical_name="Tata Consultancy Services",
        aliases=["TCS"],
        ticker="TCS",
        exchange="NSE",
    )

    result = collect_market_for_company(
        db=db_session.session,
        query="TCS",
        quote=build_quote(),
    )

    db_session.session.refresh(company)
    records = db_session.session.scalars(select(SourceRecord)).all()
    kpis = db_session.session.scalars(select(KpiObservation)).all()

    assert result["status"] == "completed"
    assert result["record_found"] is True
    assert result["symbol"] == "TCS.NS"
    assert result["company"]["company_segment"] == "INDIA_LISTED"
    assert company.company_segment == "INDIA_LISTED"
    assert len(records) == 1
    assert records[0].record_type == "market_price_history"
    assert {kpi.kpi_name for kpi in kpis} >= {
        "market_price",
        "market_drawdown_from_52w_high_pct",
        "market_annualized_volatility_pct",
    }
    price_kpi = next(kpi for kpi in kpis if kpi.kpi_name == "market_price")
    assert price_kpi.unit == "INR"


def test_collect_market_skips_unlisted_company(db_session):
    db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"], funding_status="funded")

    result = collect_market_for_company(db=db_session.session, query="Razorpay")

    assert result["status"] == "not_applicable"
    assert result["company"]["company_segment"] == "INDIA_UNLISTED_FUNDED"
    assert "listed companies only" in result["message"]
    assert db_session.session.scalars(select(SourceRecord)).all() == []


def test_collect_market_reports_unresolved_company(db_session):
    result = collect_market_for_company(db=db_session.session, query="Unknown Startup")

    assert result["status"] == "unmatched"
    assert result["candidates"] == []


def test_collect_market_handles_missing_symbol_data(db_session):
    db_session.create_company(
        canonical_name="Zomato",
        aliases=["Zomato Ltd"],
        ticker="ZOMATO",
        exchange="NSE",
    )

    result = collect_market_for_company(db=db_session.session, query="Zomato", quote=None)

    assert result["status"] == "completed"
    assert result["record_found"] is False
    assert "stale or delisted" in result["message"]
    assert result["collection_run_id"] is not None


def test_collect_market_records_source_failure_on_timeout(db_session, monkeypatch):
    company = db_session.create_company(
        canonical_name="Infosys",
        aliases=["Infosys Limited"],
        ticker="INFY",
        exchange="NSE",
    )

    def raise_timeout(self, symbol, range_period="6mo", interval="1d"):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(market_collection.YahooMarketDataClient, "fetch_price_history", raise_timeout)

    result = collect_market_for_company(db=db_session.session, query="Infosys")

    assert result["status"] == "source_failed"
    assert result["company"]["id"] == company.id
    assert result["symbol"] == "INFY.NS"
    assert "timed out" in result["message"]


def test_collect_market_records_source_failure_on_http_error(db_session, monkeypatch):
    db_session.create_company(
        canonical_name="Freshworks",
        aliases=["Freshworks Inc"],
        country="United States",
        ticker="FRSH",
        exchange="NASDAQ",
    )

    def raise_status(self, symbol, range_period="6mo", interval="1d"):
        request = httpx.Request("GET", "https://query1.finance.yahoo.com")
        response = httpx.Response(502, request=request)
        raise httpx.HTTPStatusError("bad gateway", request=request, response=response)

    monkeypatch.setattr(market_collection.YahooMarketDataClient, "fetch_price_history", raise_status)

    result = collect_market_for_company(db=db_session.session, query="Freshworks")

    assert result["status"] == "source_failed"
    assert "HTTP 502" in result["message"]
