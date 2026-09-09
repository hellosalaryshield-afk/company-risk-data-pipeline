import httpx
import pytest
from sqlalchemy import select

from app.models.collection import KpiObservation, SourceRecord
from app.pipeline import workplace_collection
from app.pipeline.workplace_collection import WorkplaceCollectionError, collect_workplace_for_company
from tests.unit.test_glassdoor import ZS_RECORD


class SettingsStub:
    apify_token = "test-token"


class EmptySettingsStub:
    apify_token = None


def stored_kpis(db_session, company_id: int) -> dict[str, float]:
    rows = db_session.session.scalars(
        select(KpiObservation).where(KpiObservation.company_id == company_id)
    ).all()
    return {row.kpi_name: float(row.value_numeric) for row in rows}


def test_good_match_stores_record_and_kpis(db_session):
    company = db_session.create_company(canonical_name="ZS Associates", aliases=["ZS"])

    result = collect_workplace_for_company(
        db=db_session.session,
        query="ZS Associates",
        settings=EmptySettingsStub(),
        raw_record=ZS_RECORD,
    )

    assert result["status"] == "completed"
    assert result["match_confidence"] == "high"
    assert result["record_found"] is True

    kpis = stored_kpis(db_session, company.id)
    assert kpis["glassdoor_job_count"] == 232.0
    assert kpis["glassdoor_rating"] == 3.5

    record = db_session.session.scalars(select(SourceRecord)).first()
    assert record.record_type == "glassdoor_company_profile"
    assert record.confidence == "high"
    assert record.raw_data["companyId"] == 115506


def test_wrong_company_is_stored_but_never_becomes_kpis(db_session):
    """A query for Indian Zepto returns 'Zepto (Mexico)' from the live actor."""
    company = db_session.create_company(canonical_name="Zepto", aliases=["Kiranakart"])

    result = collect_workplace_for_company(
        db=db_session.session,
        query="Zepto",
        settings=EmptySettingsStub(),
        raw_record={"companyId": 999, "name": "Zepto (Mexico)", "rating": 4.0, "reviewCount": 1, "jobCount": 1},
    )

    assert result["status"] == "match_rejected"
    assert result["match_confidence"] == "low"
    assert result["kpis"] == {}
    assert stored_kpis(db_session, company.id) == {}

    # The rejected record is still kept, so the decision can be audited later.
    record = db_session.session.scalars(select(SourceRecord)).first()
    assert record is not None
    assert record.normalized_data["match_confidence"] == "low"
    assert "mexico" in record.normalized_data["match_reason"].lower()


def test_no_result_completes_without_storing_anything(db_session):
    company = db_session.create_company(canonical_name="Tiny Startup", aliases=["Tiny"])

    result = collect_workplace_for_company(
        db=db_session.session,
        query="Tiny Startup",
        settings=EmptySettingsStub(),
        raw_record=None,
    )

    assert result["status"] == "completed"
    assert result["record_found"] is False
    assert stored_kpis(db_session, company.id) == {}


def test_enriches_only_empty_company_fields(db_session):
    company = db_session.create_company(canonical_name="ZS Associates", aliases=["ZS"])
    company.industry = "Existing Industry"
    db_session.session.flush()

    collect_workplace_for_company(
        db=db_session.session,
        query="ZS Associates",
        settings=EmptySettingsStub(),
        raw_record=ZS_RECORD,
    )
    db_session.session.refresh(company)

    assert company.industry == "Existing Industry"
    assert company.website == "http://www.zs.com"


def test_missing_token_raises_before_any_call(db_session):
    db_session.create_company(canonical_name="ZS Associates", aliases=["ZS"])

    with pytest.raises(WorkplaceCollectionError, match="APIFY_TOKEN"):
        collect_workplace_for_company(
            db=db_session.session,
            query="ZS Associates",
            settings=EmptySettingsStub(),
        )


def test_timeout_is_recorded_as_a_source_failure(db_session, monkeypatch):
    company = db_session.create_company(canonical_name="ZS Associates", aliases=["ZS"])

    def raise_timeout(self, company_name):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(workplace_collection.GlassdoorClient, "fetch_company_profile", raise_timeout)

    result = collect_workplace_for_company(
        db=db_session.session,
        query="ZS Associates",
        settings=SettingsStub(),
    )

    assert result["status"] == "source_failed"
    assert result["company"]["id"] == company.id
    assert "timed out" in result["message"]


def test_http_error_is_recorded_as_a_source_failure(db_session, monkeypatch):
    db_session.create_company(canonical_name="ZS Associates", aliases=["ZS"])

    def raise_status(self, company_name):
        raise httpx.HTTPStatusError(
            "402",
            request=httpx.Request("POST", "https://api.apify.com"),
            response=httpx.Response(402),
        )

    monkeypatch.setattr(workplace_collection.GlassdoorClient, "fetch_company_profile", raise_status)

    result = collect_workplace_for_company(
        db=db_session.session,
        query="ZS Associates",
        settings=SettingsStub(),
    )

    assert result["status"] == "source_failed"
    assert "HTTP 402" in result["message"]


def test_unresolved_company_returns_candidates(db_session):
    db_session.create_company(canonical_name="ZS Associates", aliases=["ZS Consulting"])

    result = collect_workplace_for_company(
        db=db_session.session,
        query="ZS",
        settings=EmptySettingsStub(),
        raw_record=ZS_RECORD,
    )

    assert result["status"] in {"ambiguous", "unmatched"}
    assert stored_kpis(db_session, 1) == {}


def test_run_metadata_records_the_estimated_cost(db_session):
    db_session.create_company(canonical_name="ZS Associates", aliases=["ZS"])

    result = collect_workplace_for_company(
        db=db_session.session,
        query="ZS Associates",
        settings=EmptySettingsStub(),
        raw_record=ZS_RECORD,
    )

    from app.models.collection import CollectionRun

    run = db_session.session.get(CollectionRun, result["collection_run_id"])
    assert run.extra_metadata["estimated_cost_usd"] == 0.10
