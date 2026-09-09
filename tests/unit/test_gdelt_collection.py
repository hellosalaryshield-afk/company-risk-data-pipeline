import httpx
import pytest

from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.pipeline import gdelt_collection
from app.pipeline.gdelt_collection import GDELT_SOURCE_NAME, collect_gdelt_for_company
from app.sources.gdelt import GdeltFetchResult, GdeltRateLimitError, GdeltTimeline
from datetime import UTC, datetime


def sample_result() -> GdeltFetchResult:
    dates = [datetime(2026, 9, d, tzinfo=UTC) for d in (1, 2, 3, 4)]
    return GdeltFetchResult(
        company_name="Tata Consultancy Services",
        timespan="3m",
        tone=GdeltTimeline("Average Tone", dates, [2.0, 1.0, -1.0, -2.0]),
        volume=GdeltTimeline("Volume Intensity", dates, [0.01, 0.01, 0.01, 0.04]),
        articles=[{"title": "one"}, {"title": "two"}],
        raw_data={"timelinetone": {"ok": True}},
    )


def test_collect_gdelt_stores_record_and_kpis(db_session):
    company = db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["TCS"])

    result = collect_gdelt_for_company(
        db=db_session.session, query="TCS", fetch_result=sample_result()
    )

    assert result["status"] == "completed"
    assert result["record_found"] is True
    assert result["kpis"]["gdelt_worst_day_tone"] == pytest.approx(-2.0)
    assert result["kpis"]["gdelt_tone_decline"] == pytest.approx(3.0)

    records = db_session.session.query(SourceRecord).filter_by(company_id=company.id).all()
    assert len(records) == 1
    assert records[0].record_type == "gdelt_coverage"
    assert records[0].raw_data == {"timelinetone": {"ok": True}}

    kpis = db_session.session.query(KpiObservation).filter_by(company_id=company.id).all()
    assert {kpi.kpi_name for kpi in kpis} == set(result["kpis"])


def test_collect_gdelt_dates_the_observation_from_the_data_not_the_clock(db_session):
    """Point-in-time correctness: the KPI is stamped with GDELT's last reported day."""
    db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["TCS"])

    collect_gdelt_for_company(db=db_session.session, query="TCS", fetch_result=sample_result())

    kpi = db_session.session.query(KpiObservation).first()
    assert kpi.observed_at.date() == datetime(2026, 9, 4, tzinfo=UTC).date()


def test_collect_gdelt_handles_no_data(db_session):
    company = db_session.create_company(canonical_name="Zepto", aliases=["Kiranakart"])

    result = collect_gdelt_for_company(db=db_session.session, query="Zepto", fetch_result=None)

    assert result["status"] == "completed"
    assert result["record_found"] is False
    assert result["kpis"] == {}
    assert result["company"]["id"] == company.id


def test_collect_gdelt_records_a_rate_limit_as_a_soft_failure(db_session, monkeypatch):
    company = db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"])

    def raise_rate_limit(self, company_name, timespan="3m", max_articles=25):
        raise GdeltRateLimitError("GDELT rate limit reached (HTTP 429).")

    monkeypatch.setattr(gdelt_collection.GdeltClient, "fetch_company_coverage", raise_rate_limit)

    result = collect_gdelt_for_company(db=db_session.session, query="Razorpay")

    assert result["status"] == "source_failed"
    assert result["record_found"] is False
    assert "rate limit" in result["message"]

    run = db_session.session.query(CollectionRun).filter_by(company_id=company.id).one()
    assert run.status == "failed"
    assert run.extra_metadata["source_status"] == "rate_limited"
    assert run.extra_metadata["source"] == GDELT_SOURCE_NAME


def test_collect_gdelt_records_a_timeout_without_raising(db_session, monkeypatch):
    db_session.create_company(canonical_name="Nykaa", aliases=["FSN"])

    def raise_timeout(self, company_name, timespan="3m", max_articles=25):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(gdelt_collection.GdeltClient, "fetch_company_coverage", raise_timeout)

    result = collect_gdelt_for_company(db=db_session.session, query="Nykaa")

    assert result["status"] == "source_failed"
    assert "timed out" in result["message"]


def test_collect_gdelt_returns_candidates_for_an_ambiguous_name(db_session):
    db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["Tata Consultancy"])

    result = collect_gdelt_for_company(db=db_session.session, query="Tata")

    assert result["status"] == "ambiguous"
    assert result["candidates"] == ["Tata Consultancy Services"]
    assert result["kpis"] == {}
