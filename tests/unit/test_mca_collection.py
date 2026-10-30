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
        "CIN": "U72200KA2013PTC071109",
        "CompanyName": "RAZORPAY SOFTWARE PRIVATE LIMITED",
        "CompanyRegistrationdate_date": "2013-09-18",
        "CompanyStatus": "Active",
        "CompanyCategory": "Company limited by shares",
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

    monkeypatch.setattr(mca_collection.DataGovMcaClient, "fetch_by_name", raise_timeout)

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


class RecordingClient:
    def __init__(self, by_cin=None, by_name=None):
        self.by_cin = by_cin or {}
        self.by_name = by_name or {}
        self.calls = []

    def fetch_by_cin(self, cin):
        self.calls.append(("cin", cin))
        return self.by_cin.get(cin)

    def fetch_by_name(self, name):
        self.calls.append(("name", name))
        return self.by_name.get(name)


def test_lookup_tries_cin_then_legal_name_then_brand(db_session):
    company = db_session.create_company(
        canonical_name="Zepto", aliases=[], legal_name="Kiranakart Technologies Private Limited", cin="U00000KA2020PTC000000"
    )
    client = RecordingClient(by_name={"Zepto": {"CIN": "X"}})

    record = mca_collection.lookup_mca_record(client, company)

    assert record == {"CIN": "X"}
    assert client.calls == [
        ("cin", "U00000KA2020PTC000000"),
        ("name", "Kiranakart Technologies Private Limited"),
        ("name", "Zepto"),
    ]


def test_lookup_stops_at_the_first_match_and_skips_a_repeated_name(db_session):
    company = db_session.create_company(canonical_name="Infosys", aliases=[], legal_name="INFOSYS")
    client = RecordingClient(by_name={})

    assert mca_collection.lookup_mca_record(client, company) is None
    assert client.calls == [("name", "INFOSYS")]
