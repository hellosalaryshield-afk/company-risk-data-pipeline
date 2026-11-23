import pytest

from app.companies.directory import (
    clean_row,
    entity_type,
    group_duplicates,
    market_cap_crore,
    normalize_industry,
    normalize_listing,
    normalize_size,
    parse_identifiers,
)


def row(sheet="Financial Services", **fields):
    raw = {
        "Category ID": "C001", "Sub-sector": "Banks", "Listing Status": "Listed", "Track": "Main-board",
        "Size Bucket": "Large cap [AMFI 1-100]", "Company Name": "HDFC Bank Ltd", "Ticker / CIN": "HDFCBANK",
        "Market Cap / Turnover / Valuation": "₹13,06,611 Cr (Mkt Cap)", "Source URL": "https://example.com",
    }
    raw.update(fields)
    return clean_row(sheet, 2, raw)


@pytest.mark.parametrize("text, listed, expected", [
    ("HDFCBANK", True, {"nse_symbol": "HDFCBANK"}),
    ("NSE: TANLA", True, {"nse_symbol": "TANLA"}),
    ("NSE: BRITANNIA / BSE: 500825", True, {"nse_symbol": "BRITANNIA", "bse_code": "500825"}),
    ("539947.0", True, {"bse_code": "539947"}),
    ("BSE-SME: 544735", True, {"bse_code": "544735"}),
    ("NSE / 544937", True, {"bse_code": "544937"}),
    ("U65920TN2008PLC069758", False, {"cin": "U65920TN2008PLC069758"}),
    ("AAP-5614", False, {"llpin": "AAP-5614"}),
    ("AAP-5614", True, {"llpin": "AAP-5614"}),
    ("Private (Series B)", False, {"funding_stage": "Series B"}),
    ("N/A (Unlisted)", False, {}),
    ("Listed (Screener)", True, {}),
])
def test_identifiers_are_split_out_of_the_mixed_column(text, listed, expected):
    ids = parse_identifiers(text, listed)
    found = {k: v for k, v in vars(ids).items() if v}
    assert found == expected


@pytest.mark.parametrize("status, listing, board", [
    ("Listed", "listed", "mainboard"),
    ("NSE/BSE Mainboard", "listed", "mainboard"),
    ("Listed (NSE SME)", "listed", "sme"),
    ("NSE Emerge SME", "listed", "sme"),
    ("Unlisted / Private", "unlisted", None),
    ("Unlisted (Private Ltd)", "unlisted", None),
    ("MNC Subsidiary (Unlisted)", "unlisted", None),
    ("PSU / Govt", "government", None),
])
def test_thirty_listing_spellings_become_a_few_values(status, listing, board):
    assert normalize_listing(status, parse_identifiers("", False)) == (listing, board)


@pytest.mark.parametrize("bucket, listing, board, cap, tier", [
    ("Large cap [AMFI 1-100]", "listed", "mainboard", None, "large"),
    ("2=Mid cap [AMFI 101-250]", "listed", "mainboard", None, "mid"),
    ("3 - Listed Small cap", "listed", "mainboard", None, "small"),
    ("Listed SME-listed", "listed", "sme", None, "sme"),
    ("", "listed", "mainboard", 45_000.0, "mid"),
    ("Unlisted Startup Unicorn/late-stage [>=$1B or Series D+]", "unlisted", None, None, "late_stage"),
    ("Startup / Micro", "unlisted", None, None, "early_stage"),
    ("Unlisted Corporate Large [>Rs 500 cr]", "unlisted", None, None, "large"),
    ("Small / Mid-Market (₹1 Cr - ₹100 Cr)", "unlisted", None, None, "small"),
    ("Growth / Mid-Market", "unlisted", None, None, "small"),
    ("Corporate Micro [<Rs 10 cr]", "unlisted", None, None, "micro"),
    ("Private (Unreported)", "unlisted", None, None, "unknown"),
])
def test_size_buckets_fold_into_tiers(bucket, listing, board, cap, tier):
    assert normalize_size(bucket, listing, board, cap) == tier


def test_market_cap_is_read_only_from_market_cap_text():
    assert market_cap_crore("₹13,06,611 Cr (Mkt Cap)") == 1306611.0
    assert market_cap_crore("Market Cap: Rs 1,51,121 Cr") == 151121.0
    assert market_cap_crore("Turnover ₹48.3 Cr (FY25)") is None


@pytest.mark.parametrize("sector, sub, name, industry", [
    ("Financial Services", "NBFCs & Housing Finance", "Jio Financial Services Ltd", "NBFC & Lending"),
    ("Financial Services", "NBFCs & Housing Finance", "LIC Housing Finance Ltd", "Housing Finance"),
    ("Financial Services", "Capital Markets", "HDFC Asset Management Company Ltd", "Asset & Wealth Management"),
    ("Financial Services", "Banking", "Saraswat Co-operative Bank Ltd", "Banks"),
    ("Financial Services", "Credit-Tech / MSME", "Indifi", "Fintech & Payments"),
    ("Information Technology", "Fabless Semiconductor / IoT Radios & Timing ICs", "X", "Semiconductors & Electronics Manufacturing"),
    ("Information Technology", "Cloud Security, Privacy & AppSec", "X", "Cybersecurity"),
    ("Information Technology", "Agri Drones (Type Certified)", "X", "Robotics, Drones & Aerospace"),
    ("Information Technology", "Custom Software Development", "X", "IT Services & Consulting"),
    ("Healthcare", "Pharmaceuticals", "Sun Pharma", "Pharmaceuticals"),
])
def test_long_sub_sector_lists_fold_into_short_industries(sector, sub, name, industry):
    assert normalize_industry(sector, sub, name) == industry


@pytest.mark.parametrize("name, status, ticker, kind", [
    ("HDFC Bank Ltd", "Listed", "HDFCBANK", "company"),
    ("Barclays Bank Plc (India)", "Unlisted", "N/A (RBI Scheduled)", "foreign_bank_branch"),
    ("CloudOYE", "Unlisted", "Unlisted (India)", "company"),
    ("Assam Gramin Bank", "Unlisted", "N/A (RBI Scheduled RRB)", "rural_bank"),
    ("Saraswat Co-operative Bank Ltd", "Unlisted", "Scheduled Co-op", "cooperative_bank"),
    ("Rajarshi Shahu Government Servants' Co-op. Bank Ltd., Kolhapur", "Unlisted", "", "cooperative_bank"),
    ("Indian Farmers Fertiliser Cooperative Ltd (IFFCO)", "Unlisted", "", "cooperative"),
    ("Coaching Federation Of India", "Unlisted", "", "association"),
    ("Software Technology Parks of India (STPI)", "PSU / Govt", "Govt Society", "government_body"),
])
def test_entities_with_little_public_data_are_labelled(name, status, ticker, kind):
    assert entity_type(name, status, ticker) == kind


def test_duplicates_merge_on_ticker_and_keep_the_company_s_own_sector():
    rows = [
        row(sheet="Diversified & Conglomerates", **{"Company Name": "Jio Financial Services Ltd", "Ticker / CIN": "JIOFIN",
                                                    "Sub-sector": "Group Holding Companies", "Category ID": "C716"}),
        row(**{"Company Name": "Jio Financial Services Ltd", "Ticker / CIN": "JIOFIN",
               "Sub-sector": "NBFCs & Housing Finance", "Category ID": "C014"}),
        row(**{"Company Name": "HDFC Bank Ltd"}),
    ]

    entries = group_duplicates(rows)

    assert len(entries) == 2
    jio = next(e for e in entries if e.primary.name.startswith("Jio"))
    assert jio.primary.sector == "Financial Services" and jio.others[0].sector == "Diversified & Conglomerates"


def test_a_brand_name_in_brackets_still_matches_on_ticker():
    rows = [row(**{"Company Name": "Billionbrains Garage Ventures Ltd (Groww)", "Ticker / CIN": "GROWW"}),
            row(**{"Company Name": "Groww", "Ticker / CIN": "NSE: GROWW"})]

    assert len(group_duplicates(rows)) == 1


def test_peer_group_and_coverage():
    bank = row()
    startup = row(**{"Company Name": "Kitebird", "Listing Status": "Unlisted", "Ticker / CIN": "N/A",
                     "Size Bucket": "Startup Small/early-stage [Seed-Series A]"})

    assert bank.peer_group == "Financial Services / Banks / listed / large" and bank.coverage == "full"
    assert startup.peer_group == "Financial Services / Banks / unlisted / early_stage" and startup.coverage == "basic"
