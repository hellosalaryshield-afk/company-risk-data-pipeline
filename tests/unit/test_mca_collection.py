from datetime import date

import httpx

from app.pipeline import mca_collection
from app.pipeline.mca_collection import collect_mca_for_company


class SettingsStub:
    data_gov_api_key = "test-key"


class EmptySettingsStub:
    data_gov_api_key = None


def test_collect_mca_for_company_stores_source_record_and_updates_company(db_session):
    company = db_session.create_company(
        canonical_name="Razorpay",
        aliases=["Razorpay Software Private Limited"],
    )
    raw_record = {
        "cin": "U72200KA2013PTC071109",
        "company_name": "RAZORPAY SOFTWARE PRIVATE LIMITED",
        "date_of_incorporation": "2013-09-18",
        "company_status": "Active",
        "company_category": "Company limited by shares",
    }

    result = collect_mca_for_company(
        db=db_session.session,
        query="Razorpay",
        settings=EmptySettingsStub(),
        raw_record=raw_record,
    )

    db_session.session.refresh(company)

    assert result["status"] == "completed"
    assert result["record_found"] is True
    assert company.cin == "U72200KA2013PTC071109"
    assert company.legal_name == "RAZORPAY SOFTWARE PRIVATE LIMITED"
    assert company.incorporation_date == date(2013, 9, 18)
    assert company.company_status == "Active"


def test_collect_mca_for_company_handles_no_record(db_session):
    company = db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"])

    result = collect_mca_for_company(
        db=db_session.session,
        query="Razorpay",
        settings=EmptySettingsStub(),
        raw_record=None,
    )

    assert result["status"] == "completed"
    assert result["record_found"] is False
    assert result["company"]["id"] == company.id


def test_collect_mca_records_source_failure_on_timeout(db_session, monkeypatch):
    company = db_session.create_company(canonical_name="Paytm", aliases=["One97 Communications"])

    def raise_timeout(self, company_name):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(mca_collection.DataGovMcaClient, "fetch_company_master_data", raise_timeout)

    result = collect_mca_for_company(
        db=db_session.session,
        query="Paytm",
        settings=SettingsStub(),
    )

    assert result["status"] == "source_failed"
    assert result["company"]["id"] == company.id
    assert result["record_found"] is False
    assert result["collection_run_id"] is not None
    assert "timed out" in result["message"]
