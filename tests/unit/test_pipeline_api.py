import pytest
from fastapi.testclient import TestClient

from app.database.session import get_db_session
from app.main import app
from app.models.source import DataSource
from app.pipeline import company_collection


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db_session] = lambda: db_session.session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def stub_sources(monkeypatch):
    monkeypatch.setattr(
        company_collection,
        "collect_news_for_company",
        lambda **kwargs: {"status": "completed", "records_stored": 2, "kpis": {"news_article_count": 2.0}},
    )
    monkeypatch.setattr(
        company_collection,
        "collect_mca_for_company",
        lambda **kwargs: {"status": "source_failed", "message": "timed out"},
    )
    monkeypatch.setattr(
        company_collection,
        "collect_market_for_company",
        lambda **kwargs: {"status": "completed", "record_found": True, "kpis": {"market_price": 2255.5}},
    )


def test_company_collection_endpoint_runs_applicable_sources(client, db_session, stub_sources):
    db_session.create_company(
        canonical_name="Tata Consultancy Services",
        aliases=["TCS"],
        ticker="TCS",
        exchange="NSE",
    )

    response = client.post("/collections/company", json={"company_name": "TCS"})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "completed"
    assert body["segment"]["segment"] == "INDIA_LISTED"
    assert body["sources_failed"] == ["data_gov_mca_company_master"]
    assert body["kpis"]["market_price"] == pytest.approx(2255.5)


def test_company_collection_endpoint_returns_candidates_when_ambiguous(client, db_session, stub_sources):
    db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["Tata Consultancy"])

    body = client.post("/collections/company", json={"company_name": "Tata"}).json()

    assert body["status"] == "ambiguous"
    assert body["candidates"][0]["canonical_name"] == "Tata Consultancy Services"


def test_sources_endpoint_lists_registered_sources(client, db_session):
    db_session.session.add(DataSource(name="newsapi", source_type="api", requires_auth=True))
    db_session.session.commit()

    body = client.get("/sources").json()

    assert [item["name"] for item in body] == ["newsapi"]
    assert body[0]["requires_auth"] is True


def test_collection_runs_endpoint_returns_recent_runs(client, db_session, stub_sources):
    db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"], funding_status="funded")
    client.post("/collections/company", json={"company_name": "Razorpay"})

    body = client.get("/collection-runs").json()

    assert isinstance(body, list)


def test_summary_endpoint_returns_report_data(client, db_session):
    company = db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")

    body = client.get(f"/companies/{company.id}/summary").json()

    assert body["company"]["canonical_name"] == "Nykaa"
    assert body["segment"]["segment"] == "INDIA_LISTED"
    assert "coverage" in body


def test_html_report_endpoint_returns_html(client, db_session):
    company = db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")

    response = client.get(f"/companies/{company.id}/report")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Nykaa" in response.text
    assert "not a risk score" in response.text


def test_pdf_report_endpoint_returns_a_pdf(client, db_session):
    company = db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")

    response = client.get(f"/companies/{company.id}/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert "nykaa_signal_report.pdf" in response.headers["content-disposition"]


def test_report_endpoints_return_404_for_unknown_company(client):
    assert client.get("/companies/9999/summary").status_code == 404
    assert client.get("/companies/9999/report").status_code == 404
    assert client.get("/companies/9999/report.pdf").status_code == 404
