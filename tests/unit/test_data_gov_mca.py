from datetime import date

import httpx

from app.sources import data_gov_mca
from app.sources.data_gov_mca import DataGovMcaClient, normalize_mca_record, parse_mca_date

# A real record's shape, as returned by the data.gov.in API on 2026-10-30.
API_RECORD = {
    "CIN": "L22210MH1995PLC084781",
    "CompanyName": "TATA CONSULTANCY SERVICES LIMITED",
    "CompanyROCcode": "ROC Mumbai",
    "CompanyCategory": "Company limited by shares",
    "CompanySubCategory": "Non-government company",
    "CompanyClass": "Public",
    "AuthorizedCapital": "4600000000.00",
    "PaidupCapital": "3618000000.00",
    "CompanyRegistrationdate_date": "1995-01-19",
    "Registered_Office_Address": "9TH FLOOR NIRMAL BUILDING,NARIMAN POINT,MUMBAI,Maharashtra,400021-India",
    "Listingstatus": "Listed",
    "CompanyStatus": "Active",
    "CompanyStateCode": "maharashtra",
    "CompanyIndian/Foreign Company": "91",
    "nic_code": "NA",
    "CompanyIndustrialClassification": "Business Services",
}


def test_normalize_mca_record_maps_the_api_field_names() -> None:
    normalized = normalize_mca_record(API_RECORD)

    assert normalized["cin"] == "L22210MH1995PLC084781"
    assert normalized["company_name"] == "TATA CONSULTANCY SERVICES LIMITED"
    assert normalized["incorporation_date"] == "1995-01-19"
    assert normalized["company_status"] == "Active"
    assert normalized["company_class"] == "Public"
    assert normalized["registered_state"] == "maharashtra"
    assert normalized["listing_status"] == "Listed"


def test_na_values_are_treated_as_missing() -> None:
    assert normalize_mca_record({**API_RECORD, "CompanyIndustrialClassification": "NA"})["industrial_classification"] is None


def test_parse_mca_date_supports_multiple_formats() -> None:
    assert parse_mca_date("2013-09-18") == date(2013, 9, 18)
    assert parse_mca_date("18/09/2013") == date(2013, 9, 18)
    assert parse_mca_date("18-09-2013") == date(2013, 9, 18)
    assert parse_mca_date("bad date") is None


def capture_get(monkeypatch, records):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append({**params, "_headers": headers})
        return httpx.Response(200, json={"records": records}, request=httpx.Request("GET", url))

    monkeypatch.setattr(data_gov_mca.httpx, "get", get)
    return calls


def test_name_lookup_uses_the_api_field_and_registry_casing(monkeypatch) -> None:
    calls = capture_get(monkeypatch, [API_RECORD])

    record = DataGovMcaClient("k").fetch_by_name("  Tata Consultancy   Services Limited ")

    assert record["CIN"] == "L22210MH1995PLC084781"
    assert calls[0]["filters[CompanyName]"] == "TATA CONSULTANCY SERVICES LIMITED"
    assert calls[0]["api-key"] == "k"


def test_requests_never_use_the_default_python_user_agent(monkeypatch) -> None:
    """data.gov.in silently hangs on python-httpx and python-requests user agents."""
    calls = capture_get(monkeypatch, [])

    DataGovMcaClient("k").fetch_by_name("Infosys Limited")

    user_agent = calls[0]["_headers"]["User-Agent"]
    assert "python" not in user_agent.lower()


def test_cin_lookup_uses_the_cin_field(monkeypatch) -> None:
    calls = capture_get(monkeypatch, [])

    assert DataGovMcaClient("k").fetch_by_cin(" l22210mh1995plc084781") is None
    assert calls[0]["filters[CIN]"] == "L22210MH1995PLC084781"
