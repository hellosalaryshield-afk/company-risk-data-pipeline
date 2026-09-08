from app.companies.segments import (
    FOREIGN_LISTED,
    FOREIGN_UNLISTED_FUNDED,
    INDIA_LISTED,
    INDIA_UNLISTED_FUNDED,
    INDIA_UNLISTED_NON_FUNDED,
    classify_company,
)


def test_india_listed_company_is_classified_from_ticker_and_exchange():
    result = classify_company(country="India", ticker="TCS", exchange="NSE")

    assert result.segment == INDIA_LISTED
    assert result.listing_status == "listed"
    assert result.confidence == "high"


def test_foreign_listed_company_is_classified_from_country():
    result = classify_company(country="United States", ticker="FRSH", exchange="NASDAQ")

    assert result.segment == FOREIGN_LISTED
    assert result.geography == "foreign"


def test_unlisted_funded_indian_company_uses_funding_status():
    result = classify_company(country="India", funding_status="funded")

    assert result.segment == INDIA_UNLISTED_FUNDED
    assert result.listing_status == "unlisted"


def test_unlisted_non_funded_indian_company_uses_funding_status():
    result = classify_company(country="India", funding_status="not_funded")

    assert result.segment == INDIA_UNLISTED_NON_FUNDED


def test_unlisted_foreign_funded_company_is_classified():
    result = classify_company(country="Singapore", funding_status="funded")

    assert result.segment == FOREIGN_UNLISTED_FUNDED


def test_unlisted_company_without_funding_status_is_left_undetermined():
    result = classify_company(country="India")

    assert result.segment is None
    assert result.funding_status == "unknown"
    assert result.confidence == "low"
    assert any("funding status unknown" in reason for reason in result.reasons)


def test_cin_implies_india_when_country_is_missing():
    result = classify_company(cin="U72200KA2013PTC071109", funding_status="funded")

    assert result.segment == INDIA_UNLISTED_FUNDED
    assert result.geography == "india"


def test_indian_exchange_implies_india_when_country_is_missing():
    result = classify_company(ticker="INFY", exchange="NSE")

    assert result.segment == INDIA_LISTED


def test_unknown_geography_defaults_to_foreign_with_low_confidence():
    result = classify_company(ticker="ACME", exchange="NASDAQ")

    assert result.segment == FOREIGN_LISTED
    assert result.confidence == "low"


def test_blank_ticker_is_treated_as_unlisted():
    result = classify_company(country="India", ticker="   ", exchange="NSE", funding_status="funded")

    assert result.segment == INDIA_UNLISTED_FUNDED


def test_classify_segment_persists_segment_on_company(db_session):
    company = db_session.create_company(
        canonical_name="Infosys",
        aliases=["Infosys Limited"],
        ticker="INFY",
        exchange="NSE",
    )

    from app.companies.repository import CompanyRepository

    classification = CompanyRepository(db_session.session).classify_segment(company)

    assert classification.segment == INDIA_LISTED
    assert company.company_segment == INDIA_LISTED
