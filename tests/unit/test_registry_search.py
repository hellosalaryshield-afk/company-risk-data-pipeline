from datetime import date

from app.companies.normalization import normalize_company_name
from app.companies.registry_search import as_api_record, compact, find_in_registry, fuzzy_candidates
from app.models.registry import McaCompany
from app.pipeline import mca_collection
from app.pipeline.mca_collection import collect_mca_for_company


def add(db_session, cin, name, status="Active", state="karnataka"):
    company = McaCompany(
        cin=cin,
        company_name=name,
        normalized_name=normalize_company_name(name),
        company_status=status,
        company_class="Public",
        registered_state=state,
        registration_date=date(2020, 5, 1),
    )
    db_session.session.add(company)
    db_session.session.flush()
    return company


def test_cin_wins(db_session):
    add(db_session, "U46909MH2020PLC351339", "ZEPTO LIMITED")

    match = find_in_registry(db_session.session, ["Something Else"], cin="u46909mh2020plc351339")

    assert match.status == "matched"
    assert match.matched_via == "cin"
    assert match.company.company_name == "ZEPTO LIMITED"


def test_a_brand_matches_its_registered_name_once_legal_suffixes_are_dropped(db_session):
    add(db_session, "U46909MH2020PLC351339", "ZEPTO LIMITED")
    add(db_session, "U62099KA2013PLC097389", "RAZORPAY SOFTWARE LIMITED")

    zepto = find_in_registry(db_session.session, ["Kiranakart Technologies Private Limited", "Zepto"])
    razorpay = find_in_registry(db_session.session, ["Razorpay Software Private Limited"])

    assert zepto.company.cin == "U46909MH2020PLC351339"
    assert zepto.matched_via == "exact_name"
    assert razorpay.company.company_name == "RAZORPAY SOFTWARE LIMITED"


def test_an_active_company_is_preferred_over_closed_ones_with_the_same_name(db_session):
    add(db_session, "U00000KA2010PTC000001", "ACME PRIVATE LIMITED", status="Strike Off")
    add(db_session, "U00000KA2015PLC000002", "ACME LIMITED", status="Active")

    match = find_in_registry(db_session.session, ["Acme"])

    assert match.status == "matched"
    assert match.company.cin == "U00000KA2015PLC000002"


def test_equally_good_matches_are_ambiguous_not_guessed(db_session):
    add(db_session, "U00000KA2015PLC000002", "ACME LIMITED", state="karnataka")
    add(db_session, "U00000MH2016PTC000003", "ACME PRIVATE LIMITED", state="maharashtra")

    match = find_in_registry(db_session.session, ["Acme"])

    assert match.status == "ambiguous"
    assert match.company is None
    assert {c.registered_state for c in match.candidates} == {"karnataka", "maharashtra"}


def test_unknown_names_and_empty_input_are_unmatched(db_session):
    add(db_session, "U00000KA2015PLC000002", "ACME LIMITED")

    assert find_in_registry(db_session.session, ["Nothing Like It"]).status == "unmatched"
    assert find_in_registry(db_session.session, [None, ""]).status == "unmatched"


def test_compact_ignores_spacing_differences():
    assert compact("One97 Communications Limited") == compact("ONE 97 COMMUNICATIONS LIMITED")


def test_fuzzy_search_is_postgresql_only(db_session):
    add(db_session, "U00000KA2015PLC000002", "ACME LIMITED")

    assert fuzzy_candidates(db_session.session, "acme") == []


def test_registry_row_converts_to_api_field_names(db_session):
    record = as_api_record(add(db_session, "U46909MH2020PLC351339", "ZEPTO LIMITED"))

    assert record["CIN"] == "U46909MH2020PLC351339"
    assert record["CompanyName"] == "ZEPTO LIMITED"
    assert record["CompanyRegistrationdate_date"] == "2020-05-01"


def test_mca_collection_uses_the_registry_before_the_api(db_session, monkeypatch):
    company = db_session.create_company(
        canonical_name="Zepto", aliases=["Kiranakart"], legal_name="Kiranakart Technologies Private Limited"
    )
    add(db_session, "U46909MH2020PLC351339", "ZEPTO LIMITED")

    def api_must_not_be_called(*args, **kwargs):
        raise AssertionError("The API was called although the registry had a match.")

    monkeypatch.setattr(mca_collection, "lookup_mca_record", api_must_not_be_called)

    class Settings:
        data_gov_api_key = None

    result = collect_mca_for_company(db=db_session.session, query="Zepto", settings=Settings())

    db_session.session.refresh(company)
    assert result["record_found"] is True
    assert result["matched_via"] == "registry:exact_name"
    assert company.cin == "U46909MH2020PLC351339"
    assert company.legal_name == "ZEPTO LIMITED"
