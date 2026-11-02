from datetime import date, datetime
from typing import Any

import httpx

DATA_GOV_MCA_ENDPOINT = "https://api.data.gov.in/resource/4dbe5667-7b6b-41d7-82af-211562424d9a"

# data.gov.in never answers requests carrying a default Python library user agent such as
# python-httpx or python-requests: the connection just hangs until it times out. That was
# the "outage" recorded in docs/source-feasibility.md. Any other agent, including this
# honest one, is answered in under a second (measured 2026-10-30).
USER_AGENT = "CompanyRiskDataPipeline/1.0"


class DataGovMcaClient:
    """MCA company master data (about 3.7 million companies) through data.gov.in.

    Filters are exact and case-sensitive: "RAZORPAY SOFTWARE PRIVATE LIMITED" matches, but
    any other spelling returns nothing. Field names are the API's own, e.g. CompanyName.
    """

    def __init__(self, api_key: str, timeout_seconds: float = 30.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_by_cin(self, cin: str) -> dict[str, Any] | None:
        return self._first_match("CIN", cin.strip().upper())

    def fetch_by_name(self, company_name: str) -> dict[str, Any] | None:
        # The registry stores names in upper case with single spaces.
        return self._first_match("CompanyName", " ".join(company_name.upper().split()))

    def fetch_company_master_data(self, company_name: str) -> dict[str, Any] | None:
        return self.fetch_by_name(company_name)

    def _first_match(self, field: str, value: str) -> dict[str, Any] | None:
        params = {
            "api-key": self.api_key,
            "format": "json",
            "limit": 5,
            f"filters[{field}]": value,
        }
        response = httpx.get(
            DATA_GOV_MCA_ENDPOINT,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        records = response.json().get("records", [])
        return records[0] if records else None


def parse_mca_date(value: str | None) -> date | None:
    if not value:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def normalize_mca_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "cin": pick(record, "CIN"),
        "company_name": pick(record, "CompanyName"),
        "incorporation_date": pick(record, "CompanyRegistrationdate_date"),
        "company_status": pick(record, "CompanyStatus"),
        "company_category": pick(record, "CompanyCategory"),
        "company_class": pick(record, "CompanyClass"),
        "company_subcategory": pick(record, "CompanySubCategory"),
        "authorized_capital": pick(record, "AuthorizedCapital"),
        "paidup_capital": pick(record, "PaidupCapital"),
        "registered_state": pick(record, "CompanyStateCode"),
        "roc": pick(record, "CompanyROCcode"),
        "registered_office_address": pick(record, "Registered_Office_Address"),
        "industrial_classification": pick(record, "CompanyIndustrialClassification"),
        "listing_status": pick(record, "Listingstatus"),
    }


def pick(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, "", "NA"):
            return record[key]
    return None
