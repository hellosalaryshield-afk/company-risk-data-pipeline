from datetime import date

import pytest

from scripts.load_mca_bulk import COLUMNS, is_closed, to_row


@pytest.mark.parametrize(
    "status",
    [
        "Strike Off",
        "Strike Off-AwaitingPublication",
        "Amalgamated",
        "Converted to LLP",
        "Converted to LLP and Dissolved",
        "Converted and Dissolved",
        "Dissolved (Liquidated)",
        "Dissolved under section 54",
        "Dissolved",
    ],
)
def test_closed_companies_are_skipped(status):
    assert is_closed(status) is True


@pytest.mark.parametrize(
    "status",
    [
        "Active",
        "Under process of striking off",
        "Under Process of Strike-Off",
        "Under Liquidation",
        "Under CIRP",
        "Inactive for e-filing",
        "Dormant under section 455",
        "Vanished",
        "A status added next year",
    ],
)
def test_live_and_troubled_companies_are_kept(status):
    assert is_closed(status) is False


def test_to_row_cleans_and_normalizes_a_record():
    row = to_row(
        {
            "CIN": " u46909mh2020plc351339 ",
            "CompanyName": "ZEPTO LIMITED",
            "CompanyStatus": "Active",
            "CompanyClass": "Public",
            "CompanyStateCode": "maharashtra",
            "CompanyRegistrationdate_date": "2020-05-01",
            "Listingstatus": "Unlisted",
            "CompanyIndustrialClassification": "NA",
        }
    )
    values = dict(zip(COLUMNS, row))

    assert values["cin"] == "U46909MH2020PLC351339"
    assert values["normalized_name"] == "zepto"
    assert values["registration_date"] == date(2020, 5, 1)
    assert values["industrial_classification"] is None


def test_to_row_rejects_records_without_a_cin_or_name():
    assert to_row({"CIN": "", "CompanyName": "X LIMITED"}) is None
    assert to_row({"CIN": "U1", "CompanyName": "NA"}) is None
