import pytest

from app.pipeline import company_collection
from app.pipeline.company_collection import collect_company_data
from app.pipeline.news_collection import NewsCollectionError


class SettingsStub:
    news_api_key = "test-key"
    data_gov_api_key = "test-key"


@pytest.fixture
def stub_sources(monkeypatch):
    """Replace each source pipeline so the orchestrator is tested on its own."""
    calls: list[str] = []

    def fake_news(db, query, settings, days_back=30, page_size=25, **kwargs):
        calls.append("news")
        return {"status": "completed", "collection_run_id": 1, "records_stored": 3, "kpis": {"layoff_news_count": 1.0}}

    def fake_mca(db, query, settings, **kwargs):
        calls.append("mca")
        return {"status": "source_failed", "collection_run_id": 2, "message": "timed out"}

    def fake_market(db, query, range_period="6mo", **kwargs):
        calls.append("market")
        return {"status": "completed", "collection_run_id": 3, "record_found": True, "kpis": {"market_price": 10.0}}

    monkeypatch.setattr(company_collection, "collect_news_for_company", fake_news)
    monkeypatch.setattr(company_collection, "collect_mca_for_company", fake_mca)
    monkeypatch.setattr(company_collection, "collect_market_for_company", fake_market)
    return calls


def test_listed_company_runs_all_three_sources(db_session, stub_sources):
    db_session.create_company(
        canonical_name="Tata Consultancy Services",
        aliases=["TCS"],
        ticker="TCS",
        exchange="NSE",
    )

    result = collect_company_data(db=db_session.session, query="TCS", settings=SettingsStub())

    assert result["status"] == "completed"
    assert result["segment"]["segment"] == "INDIA_LISTED"
    assert stub_sources == ["news", "mca", "market"]
    assert result["sources_succeeded"] == ["newsapi", "yahoo_finance_chart"]
    assert result["sources_failed"] == ["data_gov_mca_company_master"]
    assert result["kpis"] == {"layoff_news_count": 1.0, "market_price": 10.0}


def test_unlisted_company_skips_market_source(db_session, stub_sources):
    db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"], funding_status="funded")

    result = collect_company_data(db=db_session.session, query="Razorpay", settings=SettingsStub())

    assert "market" not in stub_sources
    assert "yahoo_finance_chart" in result["sources_skipped"]
    market = next(item for item in result["sources"] if item["source"] == "yahoo_finance_chart")
    assert market["status"] == "skipped"
    assert "listed companies only" in market["message"]


def test_undetermined_segment_runs_news_only(db_session, stub_sources):
    db_session.create_company(canonical_name="Zepto", aliases=["Kiranakart"])

    result = collect_company_data(db=db_session.session, query="Zepto", settings=SettingsStub())

    assert stub_sources == ["news"]
    assert result["segment"]["segment"] is None
    assert set(result["sources_skipped"]) == {"data_gov_mca_company_master", "yahoo_finance_chart"}


def test_unresolved_company_returns_candidates_without_running_sources(db_session, stub_sources):
    db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["Tata Consultancy"])

    result = collect_company_data(db=db_session.session, query="Tata", settings=SettingsStub())

    assert result["status"] == "ambiguous"
    assert stub_sources == []
    assert [candidate["canonical_name"] for candidate in result["candidates"]] == ["Tata Consultancy Services"]


def test_missing_api_key_is_reported_without_stopping_other_sources(db_session, monkeypatch):
    db_session.create_company(
        canonical_name="Infosys",
        aliases=["Infosys Limited"],
        ticker="INFY",
        exchange="NSE",
    )

    def raise_missing_key(db, query, settings, days_back=30, page_size=25, **kwargs):
        raise NewsCollectionError("NEWS_API_KEY is not configured.")

    def fake_mca(db, query, settings, **kwargs):
        return {"status": "completed", "record_found": False}

    def fake_market(db, query, range_period="6mo", **kwargs):
        return {"status": "completed", "record_found": True, "kpis": {"market_price": 1.0}}

    monkeypatch.setattr(company_collection, "collect_news_for_company", raise_missing_key)
    monkeypatch.setattr(company_collection, "collect_mca_for_company", fake_mca)
    monkeypatch.setattr(company_collection, "collect_market_for_company", fake_market)

    result = collect_company_data(db=db_session.session, query="Infosys", settings=SettingsStub())

    news = next(item for item in result["sources"] if item["source"] == "newsapi")
    assert news["status"] == "not_configured"
    assert "NEWS_API_KEY" in news["message"]
    assert "yahoo_finance_chart" in result["sources_succeeded"]


def test_unexpected_source_error_does_not_stop_the_run(db_session, monkeypatch):
    db_session.create_company(canonical_name="Wipro", aliases=["Wipro Limited"], ticker="WIPRO", exchange="NSE")

    def explode(db, query, settings, days_back=30, page_size=25, **kwargs):
        raise ValueError("unexpected boom")

    monkeypatch.setattr(company_collection, "collect_news_for_company", explode)
    monkeypatch.setattr(
        company_collection, "collect_mca_for_company", lambda **kwargs: {"status": "completed", "record_found": False}
    )
    monkeypatch.setattr(
        company_collection,
        "collect_market_for_company",
        lambda **kwargs: {"status": "completed", "record_found": True, "kpis": {"market_price": 2.0}},
    )

    result = collect_company_data(db=db_session.session, query="Wipro", settings=SettingsStub())

    news = next(item for item in result["sources"] if item["source"] == "newsapi")
    assert news["status"] == "error"
    assert "ValueError" in news["message"]
    assert result["status"] == "completed"
