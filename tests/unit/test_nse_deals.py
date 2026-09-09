from datetime import date

import pytest

from app.models.collection import KpiObservation, SourceRecord
from app.pipeline.deals_collection import collect_deals_for_registry, nse_symbol
from app.sources.deal_signals import deal_summary, extract_deal_kpis
from app.sources.nse_deals import Deal, group_by_symbol, parse_deal_date, parse_deals, parse_number

# Header and rows copied verbatim from the live file on 2026-09-09.
REAL_CSV = (
    "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,"
    "Trade Price / Wght. Avg. Price,Remarks\n"
    "09-SEP-2026,AASTHA,Aastha Spintex Limited,JAGID VANITABEN,BUY,243493,80.03,-\n"
    "09-SEP-2026,AASTHA,Aastha Spintex Limited,BLOOMCREST TRADE PRIVATE LIMITED,SELL,500000,80.00,-\n"
    "09-SEP-2026,PCJEWELLER,PC Jeweller Limited,SOME FUND,SELL,1000000,12.50,-\n"
)

EMPTY_CSV = (
    "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,"
    "Trade Price / Wght. Avg. Price\nNO RECORDS,,,,,,\n"
)


def make_deal(symbol="AASTHA", side="BUY", quantity=100.0, client="A CLIENT"):
    return Deal(
        deal_date=date(2026, 9, 9),
        symbol=symbol,
        security_name="Aastha Spintex Limited",
        client_name=client,
        side=side,
        quantity=quantity,
        price=80.0,
        deal_type="bulk",
        raw_data={},
    )


def test_parse_deal_date_reads_the_nse_format():
    assert parse_deal_date("09-SEP-2026") == date(2026, 9, 9)
    assert parse_deal_date("not a date") is None
    assert parse_deal_date(None) is None


def test_parse_number_handles_thousands_and_blanks():
    assert parse_number("1,234.5") == pytest.approx(1234.5)
    assert parse_number("-") is None
    assert parse_number("") is None
    assert parse_number(None) is None


def test_parse_deals_reads_the_real_file_shape():
    deals = parse_deals(REAL_CSV, deal_type="bulk")

    assert len(deals) == 3
    assert deals[0].symbol == "AASTHA"
    assert deals[0].side == "BUY"
    assert deals[0].quantity == pytest.approx(243493)
    assert deals[0].deal_type == "bulk"


def test_parse_deals_treats_no_records_as_an_empty_day():
    """NSE publishes an empty day as a NO RECORDS row, not an empty file."""
    assert parse_deals(EMPTY_CSV, deal_type="block") == []


def test_parse_deals_skips_rows_missing_a_symbol_or_quantity():
    payload = (
        "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,"
        "Trade Price / Wght. Avg. Price\n"
        "09-SEP-2026,,No Symbol Ltd,X,BUY,100,10\n"
        "09-SEP-2026,GOOD,Good Ltd,Y,BUY,,10\n"
        "09-SEP-2026,GOOD,Good Ltd,Y,BUY,50,10\n"
    )

    deals = parse_deals(payload, deal_type="bulk")

    assert len(deals) == 1
    assert deals[0].quantity == pytest.approx(50)


def test_group_by_symbol_collects_each_ticker():
    grouped = group_by_symbol([make_deal("A"), make_deal("A"), make_deal("B")])

    assert set(grouped) == {"A", "B"}
    assert len(grouped["A"]) == 2


def test_extract_deal_kpis_measures_selling_pressure():
    deals = [make_deal(side="BUY", quantity=250.0), make_deal(side="SELL", quantity=750.0, client="B")]

    kpis = extract_deal_kpis(deals)

    assert kpis["deal_count"] == pytest.approx(2)
    assert kpis["deal_buy_quantity"] == pytest.approx(250)
    assert kpis["deal_sell_quantity"] == pytest.approx(750)
    assert kpis["deal_net_quantity"] == pytest.approx(-500)
    assert kpis["deal_sell_share_pct"] == pytest.approx(75.0)
    assert kpis["deal_distinct_clients"] == pytest.approx(2)


def test_extract_deal_kpis_returns_nothing_for_no_deals():
    assert extract_deal_kpis([]) == {}


def test_deal_summary_names_the_security_and_day():
    summary = deal_summary([make_deal()])

    assert summary["symbol"] == "AASTHA"
    assert summary["deal_date"] == "2026-09-09"
    assert summary["deal_types"] == ["bulk"]


def test_only_nse_tickers_are_matched(db_session):
    """A BSE or foreign ticker could collide with an unrelated NSE symbol."""
    nse = db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")
    foreign = db_session.create_company(canonical_name="Freshworks", aliases=["FRSH Inc"], ticker="FRSH", exchange="NASDAQ")
    unlisted = db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"])

    assert nse_symbol(nse) == "NYKAA"
    assert nse_symbol(foreign) is None
    assert nse_symbol(unlisted) is None


def test_collect_deals_attaches_matching_deals_to_companies(db_session):
    company = db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")
    db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"])

    deals = [
        make_deal(symbol="NYKAA", side="BUY", quantity=1000.0),
        make_deal(symbol="NYKAA", side="SELL", quantity=3000.0, client="SELLER"),
        make_deal(symbol="UNRELATED", side="SELL", quantity=999.0),
    ]

    result = collect_deals_for_registry(db_session.session, deals=deals)

    assert result["status"] == "completed"
    assert result["companies_matched"] == 1
    assert result["matches"][0]["symbol"] == "NYKAA"
    assert result["matches"][0]["kpis"]["deal_sell_share_pct"] == pytest.approx(75.0)

    records = db_session.session.query(SourceRecord).filter_by(company_id=company.id).all()
    assert len(records) == 1
    assert records[0].record_type == "nse_deals"


def test_deal_kpis_are_dated_from_the_deal_day_not_collection_time(db_session):
    """This is what makes the source usable in a point-in-time backtest."""
    company = db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")

    collect_deals_for_registry(db_session.session, deals=[make_deal(symbol="NYKAA")])

    kpi = db_session.session.query(KpiObservation).filter_by(company_id=company.id).first()
    assert kpi.published_at.date() == date(2026, 9, 9)
    assert kpi.period_end.date() == date(2026, 9, 9)


def test_a_day_with_no_matching_company_is_a_normal_result(db_session):
    db_session.create_company(canonical_name="Nykaa", aliases=["FSN"], ticker="NYKAA", exchange="NSE")

    result = collect_deals_for_registry(db_session.session, deals=[make_deal(symbol="SOMEONEELSE")])

    assert result["status"] == "completed"
    assert result["companies_matched"] == 0
    assert result["matches"] == []
