from datetime import date, datetime
from typing import Any

import httpx

DATA_GOV_MCA_ENDPOINT = "https://api.data.gov.in/resource/4dbe5667-7b6b-41d7-82af-211562424d9a"


class DataGovMcaClient:
    def __init__(self, api_key: str, timeout_seconds: float = 8.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_company_master_data(self, company_name: str) -> dict[str, Any] | None:
        params = {
            "api-key": self.api_key,
            "format": "json",
            "limit": 10,
            "filters[company_name]": company_name.strip().upper(),
        }
        response = httpx.get(DATA_GOV_MCA_ENDPOINT, params=params, timeout=self.timeout_seconds)
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
        "cin": pick(record, "cin", "CIN"),
        "company_name": pick(record, "company_name", "Company Name", "company"),
        "incorporation_date": pick(
            record,
            "date_of_incorporation",
            "company_registration_date",
            "Date of Registration",
            "registration_date",
        ),
        "company_status": pick(record, "company_status", "company_status_for_efiling", "Company Status"),
        "company_category": pick(record, "company_category", "Company Category"),
        "company_class": pick(record, "company_class", "Company Class"),
        "company_subcategory": pick(record, "company_subcategory", "company_sub_category", "Company Sub Category"),
        "authorized_capital": pick(record, "authorized_capital", "authorised_capital", "authorized_capital_rs"),
        "paidup_capital": pick(record, "paidup_capital", "paid_up_capital", "paidup_capital_rs"),
        "registered_state": pick(record, "registered_state", "company_state_code", "Registered State"),
        "roc": pick(record, "roc", "company_roc_code", "Registrar of Companies"),
        "registered_office_address": pick(record, "registered_office_address", "Registered Office Address"),
        "industrial_classification": pick(
            record,
            "company_industrial_classification",
            "principal_business_activity",
            "Principal Business Activity",
        ),
    }


def pick(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None
