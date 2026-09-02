from datetime import date

from app.sources.data_gov_mca import normalize_mca_record, parse_mca_date


def test_normalize_mca_record_maps_common_fields() -> None:
    normalized = normalize_mca_record(
        {
            "cin": "U72200KA2013PTC071109",
            "company_name": "RAZORPAY SOFTWARE PRIVATE LIMITED",
            "date_of_incorporation": "2013-09-18",
            "company_status": "Active",
            "company_category": "Company limited by shares",
        }
    )

    assert normalized["cin"] == "U72200KA2013PTC071109"
    assert normalized["company_name"] == "RAZORPAY SOFTWARE PRIVATE LIMITED"
    assert normalized["incorporation_date"] == "2013-09-18"
    assert normalized["company_status"] == "Active"


def test_parse_mca_date_supports_multiple_formats() -> None:
    assert parse_mca_date("2013-09-18") == date(2013, 9, 18)
    assert parse_mca_date("18/09/2013") == date(2013, 9, 18)
    assert parse_mca_date("18-09-2013") == date(2013, 9, 18)
    assert parse_mca_date("bad date") is None
