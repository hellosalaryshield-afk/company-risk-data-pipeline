import pytest
from fastapi.testclient import TestClient

from app.database.session import get_db_session
from app.main import app


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db_session] = lambda: db_session.session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_segment_endpoint_classifies_a_listed_company(client, db_session):
    db_session.create_company(
        canonical_name="Tata Consultancy Services",
        aliases=["TCS"],
        ticker="TCS",
        exchange="NSE",
    )

    response = client.post("/companies/segment", json={"company_name": "TCS"})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "classified"
    assert body["segment"] == "INDIA_LISTED"
    assert body["listing_status"] == "listed"
    assert body["reasons"]


def test_segment_endpoint_reports_undetermined_without_funding_status(client, db_session):
    db_session.create_company(canonical_name="Zepto", aliases=["Kiranakart"])

    body = client.post("/companies/segment", json={"company_name": "Zepto"}).json()

    assert body["status"] == "undetermined"
    assert body["segment"] is None
    assert body["funding_status"] == "unknown"


def test_segment_endpoint_reports_unresolved_company(client):
    body = client.post("/companies/segment", json={"company_name": "Unknown Startup"}).json()

    assert body["status"] == "unmatched"
    assert body["segment"] is None


def test_market_endpoint_skips_unlisted_company(client, db_session):
    db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"], funding_status="funded")

    response = client.post("/collections/market", json={"company_name": "Razorpay"})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "not_applicable"
    assert body["company"]["company_segment"] == "INDIA_UNLISTED_FUNDED"
    assert body["kpis"] == {}


def test_market_endpoint_rejects_an_unsupported_range(client):
    response = client.post("/collections/market", json={"company_name": "TCS", "range_period": "13mo"})

    assert response.status_code == 422
