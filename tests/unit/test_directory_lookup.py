import csv

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import public
from app.companies.directory_lookup import DirectoryIndex, brand_keys, funding_status_for, load_directory, onboard
from app.database.session import get_db_session
from app.main import app
from app.models.company import Company, CompanyAlias
from app.models.directory import DirectoryCompany

COLUMNS = ["name", "sector", "industry", "listing", "board", "size_tier", "peer_group", "nse_symbol", "bse_code",
           "cin", "llpin", "funding_stage", "entity_type", "coverage", "category_id", "sub_sector", "size_bucket_raw",
           "market_cap_text", "source_url", "also_listed_under", "source_rows"]


def directory_row(name, sector="Financial Services", industry="Banks", listing="listed", size="large",
                  nse="", bse="", cin="", coverage="full", entity="company", stage=""):
    return {c: "" for c in COLUMNS} | {
        "name": name, "sector": sector, "industry": industry, "listing": listing, "size_tier": size,
        "peer_group": f"{sector} / {industry} / {listing} / {size}", "nse_symbol": nse, "bse_code": bse,
        "cin": cin, "coverage": coverage, "entity_type": entity, "funding_stage": stage,
        "board": "mainboard" if listing == "listed" else "",
    }


@pytest.fixture
def directory(db_session, tmp_path):
    rows = [
        directory_row("HDFC Bank Ltd", nse="HDFCBANK"),
        directory_row("Kotak Mahindra Bank Ltd", nse="KOTAKBANK"),
        directory_row("Kotak Securities Limited", industry="Broking & Capital Markets", listing="unlisted",
                      size="large", cin="U99999MH1994PLC134051", coverage="registry"),
        directory_row("Billionbrains Garage Ventures Ltd (Groww)", industry="Broking & Capital Markets", nse="GROWW"),
        directory_row("Think & Learn Private Limited (Byju's)", sector="Services", industry="Education & Ed-tech",
                      listing="unlisted", size="late_stage", cin="U80903KA2011PTC061427", coverage="registry"),
        directory_row("Coaching Federation Of India", sector="Services", industry="Education & Ed-tech",
                      listing="unlisted", size="unknown", coverage="not_scored", entity="association"),
        directory_row("Tata Consultancy Services Ltd", sector="Information Technology",
                      industry="IT Services & Consulting", nse="TCS"),
    ]
    rows.append(directory_row("Meta Infotech Ltd", sector="Information Technology", industry="IT Services & Consulting",
                              size="sme", bse="544441"))
    path = tmp_path / "directory.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    us_path = tmp_path / "us.csv"
    us_path.write_text(
        "name,ticker,exchange,industry,aliases\n"
        "Meta Platforms Inc,META,NASDAQ,Internet & Software,Meta|Facebook|Instagram|WhatsApp\n"
        "Amazon.com Inc,AMZN,NASDAQ,E-Commerce & Retail,Amazon|AWS\n",
        encoding="utf-8",
    )
    db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["TCS"], ticker="TCS", exchange="NSE")
    db_session.create_company(canonical_name="Amazon", ticker="AMZN", exchange="NASDAQ", country="United States")
    result = load_directory(db_session.session, path, us_path)
    yield result
    DirectoryIndex.reset()


@pytest.fixture
def client(db_session, directory, monkeypatch):
    def override():
        yield db_session.session

    app.dependency_overrides[get_db_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_loading_links_companies_that_already_exist(db_session, directory):
    tcs = db_session.session.scalar(select(DirectoryCompany).where(DirectoryCompany.nse_symbol == "TCS"))

    assert directory["rows"] == 8 and directory["us_rows"] == 2 and directory["linked_to_existing"] == 2
    assert tcs.company_id is not None


def test_search_finds_names_brands_tickers_and_typos(db_session, directory):
    index = DirectoryIndex.get(db_session.session)

    def names(query):
        hits, exact = index.search(query)
        return [h.name for h in hits], exact

    assert names("HDFC Bank") == (["HDFC Bank Ltd"], True)
    assert names("hdfcbank") == (["HDFC Bank Ltd"], True)
    assert names("Groww") == (["Billionbrains Garage Ventures Ltd (Groww)"], True)
    assert names("byju") == (["Think & Learn Private Limited (Byju's)"], False)
    assert names("hdfc bnak") == (["HDFC Bank Ltd"], False)
    kotak, exact = names("kotak")
    assert not exact and kotak[0] == "Kotak Mahindra Bank Ltd" and len(kotak) == 2  # listed and bigger first
    assert names("coaching federation") == ([], False)  # associations are never scored, so not offered


def test_brand_keys_skip_words_that_are_not_brands():
    assert brand_keys("Billionbrains Garage Ventures Ltd (Groww)") == ["groww"]
    assert brand_keys("Barclays Bank Plc (India)") == []


def test_onboarding_creates_a_scorable_company_once(db_session, directory):
    row = db_session.session.scalar(select(DirectoryCompany).where(DirectoryCompany.nse_symbol == "GROWW"))

    company = onboard(db_session.session, row)
    again = onboard(db_session.session, row)

    assert company.id == again.id and row.company_id == company.id
    assert (company.canonical_name, company.ticker, company.exchange, company.country) == \
        ("Billionbrains Garage Ventures Ltd", "GROWW", "NSE", "India")
    aliases = set(db_session.session.scalars(select(CompanyAlias.normalized_alias).where(CompanyAlias.company_id == company.id)))
    assert "groww" in aliases


def test_unlisted_startups_count_as_funded(db_session, directory):
    byjus = db_session.session.scalar(select(DirectoryCompany).where(DirectoryCompany.name.like("Think%")))
    kotak_sec = db_session.session.scalar(select(DirectoryCompany).where(DirectoryCompany.name.like("Kotak Sec%")))

    assert funding_status_for(byjus) == "funded" and funding_status_for(kotak_sec) == "not_funded"
    company = onboard(db_session.session, byjus)
    assert company.cin == "U80903KA2011PTC061427" and company.ticker is None


def test_suggestions_keep_the_directory_order_once_both_companies_exist(client, db_session):
    first = client.get("/public/companies/search", params={"q": "kotak"}).json()
    again = client.get("/public/companies/search", params={"q": "kotak"}).json()  # both now exist as companies

    assert [s["name"] for s in first["suggestions"]] == [s["name"] for s in again["suggestions"]]
    assert again["suggestions"][0]["name"] == "Kotak Mahindra Bank Ltd"


def test_public_search_reaches_the_directory(client, db_session):
    exact = client.get("/public/companies/search", params={"q": "HDFC Bank"}).json()
    several = client.get("/public/companies/search", params={"q": "kotak"}).json()
    existing = client.get("/public/companies/search", params={"q": "TCS"}).json()
    nothing = client.get("/public/companies/search", params={"q": "zzzz qqqq"}).json()

    assert exact["status"] == "matched" and exact["match"]["name"] == "HDFC Bank Ltd"
    assert several["status"] == "suggestions" and [s["name"] for s in several["suggestions"]][:2] == \
        ["Kotak Mahindra Bank Ltd", "Kotak Securities Limited"]
    assert existing["match"]["name"] == "Tata Consultancy Services"  # existing companies come first
    assert nothing["status"] == "not_found"
    assert db_session.session.scalar(select(Company).where(Company.canonical_name == "HDFC Bank Ltd")) is not None


def test_us_companies_are_found_by_brand_ticker_and_name(db_session, directory):
    index = DirectoryIndex.get(db_session.session)

    for query in ("meta", "Facebook", "instagram", "META", "Meta Platforms"):
        hits, exact = index.search(query)
        assert exact and hits[0].name == "Meta Platforms Inc", query
    # The small Indian "Meta Infotech" is still there for its full name.
    hits, exact = index.search("Meta Infotech")
    assert exact and hits[0].name == "Meta Infotech Ltd"


def test_a_us_company_is_onboarded_as_us_listed(db_session, directory):
    from app.companies.repository import CompanyRepository

    row = db_session.session.scalar(select(DirectoryCompany).where(DirectoryCompany.foreign_ticker == "META"))
    company = onboard(db_session.session, row)

    assert (company.ticker, company.exchange, company.country) == ("META", "NASDAQ", "United States")
    assert CompanyRepository(db_session.session).classify_segment(company).segment == "FOREIGN_LISTED"
    aliases = set(db_session.session.scalars(select(CompanyAlias.normalized_alias).where(CompanyAlias.company_id == company.id)))
    assert {"facebook", "instagram"} <= aliases


def test_existing_us_companies_stay_linked(db_session, directory):
    amazon = db_session.session.scalar(select(DirectoryCompany).where(DirectoryCompany.foreign_ticker == "AMZN"))
    existing = db_session.session.scalar(select(Company).where(Company.canonical_name == "Amazon"))

    assert amazon.company_id == existing.id and onboard(db_session.session, amazon).id == existing.id
