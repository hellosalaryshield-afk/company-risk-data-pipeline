from datetime import UTC, date, datetime

from sqlalchemy import select

from app.models.collection import KpiObservation, SourceRecord
from app.pipeline.financials_collection import FINANCIALS_SOURCE_NAME, collect_financials_for_company
from app.reports.scorecard import pillar_row, results_sentences
from app.scoring.config import load_config
from app.scoring.inputs import gather_inputs
from app.sources.financials import (
    RUNWAY_CAP_MONTHS,
    Financials,
    Point,
    cash_runway,
    debt_to_equity,
    financial_kpis,
    operating_margin,
    parse_fundamentals,
    revenue_growth,
)


def series(**items: list[tuple[str, float]]) -> Financials:
    return Financials("TEST", {name: [Point(date.fromisoformat(d), v, "INR") for d, v in points]
                               for name, points in items.items()})


def entry(as_of: str, value: float, period: str = "3M") -> dict:
    return {"asOfDate": as_of, "periodType": period, "currencyCode": "USD", "reportedValue": {"raw": value, "fmt": "x"}}


def test_parse_keeps_dated_values_oldest_first_and_skips_gaps():
    payload = {"timeseries": {"result": [
        {"meta": {"type": ["quarterlyTotalRevenue"]},
         "quarterlyTotalRevenue": [entry("2026-09-30", 120.0), None, entry("2025-09-30", 100.0)]},
        {"meta": {"type": ["annualTotalDebt"]}},  # Yahoo sends empty series like this
    ]}}

    fin = parse_fundamentals("QCOM", payload)

    assert [p.value for p in fin.get("quarterly", "TotalRevenue")] == [100.0, 120.0]
    assert fin.get("annual", "TotalDebt") == []
    assert fin.currency == "USD" and fin.latest_period_end == date(2026, 9, 30)


def test_revenue_growth_compares_the_same_quarter_a_year_earlier():
    fin = series(quarterlyTotalRevenue=[("2025-09-30", 100), ("2026-06-30", 130), ("2026-09-30", 92)])

    value, detail = revenue_growth(fin)

    assert value == -8.0 and detail["basis"] == "quarter vs same quarter last year"


def test_revenue_growth_falls_back_to_annual_results():
    fin = series(quarterlyTotalRevenue=[("2026-06-30", 130), ("2026-09-30", 140)],
                 annualTotalRevenue=[("2025-03-31", 200), ("2026-03-31", 103)])

    value, detail = revenue_growth(fin)

    assert value == -48.5 and detail["basis"] == "last year vs year before"


def test_operating_margin_prefers_the_last_twelve_months():
    fin = series(trailingTotalRevenue=[("2026-09-30", 1000)], trailingOperatingIncome=[("2026-09-30", -150)],
                 annualTotalRevenue=[("2026-03-31", 900)], annualOperatingIncome=[("2026-03-31", 90)])

    assert operating_margin(fin) == (-15.0, {"basis": "last twelve months", "period_end": date(2026, 9, 30)})


def test_a_company_making_cash_gets_the_runway_cap():
    fin = series(trailingOperatingCashFlow=[("2026-09-30", 50)], quarterlyCashAndCashEquivalents=[("2026-09-30", 10)])

    value, detail = cash_runway(fin)

    assert value == RUNWAY_CAP_MONTHS and detail["burning_cash"] is False


def test_runway_is_cash_divided_by_monthly_burn():
    # Burns 120 a year (10 a month) with 160 in cash and short-term investments: 16 months.
    fin = series(trailingOperatingCashFlow=[("2026-09-30", -120)],
                 quarterlyCashCashEquivalentsAndShortTermInvestments=[("2026-09-30", 160)],
                 quarterlyCashAndCashEquivalents=[("2026-09-30", 40)])

    value, detail = cash_runway(fin)

    assert value == 16.0 and detail["burning_cash"] is True and detail["cash"] == 160


def test_negative_equity_gives_no_debt_ratio_rather_than_a_safe_one():
    fin = series(quarterlyTotalDebt=[("2026-09-30", 50)], quarterlyStockholdersEquity=[("2026-09-30", -10)])

    value, detail = debt_to_equity(fin)

    assert value is None and "negative" in detail["note"]


def test_no_figures_give_no_kpis():
    assert financial_kpis(Financials("X", {})) == ({}, {})


def ola_like() -> Financials:
    return series(
        annualTotalRevenue=[("2025-03-31", 200), ("2026-03-31", 103)],
        trailingTotalRevenue=[("2026-09-30", 100)],
        trailingOperatingIncome=[("2026-09-30", -104)],
        trailingOperatingCashFlow=[("2026-09-30", -120)],
        quarterlyCashAndCashEquivalents=[("2026-09-30", 160)],
        quarterlyTotalDebt=[("2026-09-30", 60)],
        quarterlyStockholdersEquity=[("2026-09-30", 100)],
    )


def test_collection_stores_the_four_kpis_for_a_listed_company(db_session):
    company = db_session.create_company(canonical_name="Ola Electric", ticker="OLAELEC", exchange="NSE")

    result = collect_financials_for_company(db_session.session, "Ola Electric", financials=ola_like())

    assert result["status"] == "completed" and result["symbol"] == "OLAELEC.NS"
    kpis = {row.kpi_name: float(row.value_numeric) for row in db_session.session.scalars(
        select(KpiObservation).where(KpiObservation.company_id == company.id))}
    assert kpis == {"fin_revenue_growth_yoy_pct": -48.5, "fin_operating_margin_pct": -104.0,
                    "fin_cash_runway_months": 16.0, "fin_debt_to_equity": 0.6}
    record = db_session.session.scalars(select(SourceRecord).where(SourceRecord.record_type == "financial_results")).one()
    assert record.normalized_data["details"]["fin_cash_runway_months"]["burning_cash"] is True


def test_collection_skips_unlisted_companies(db_session):
    db_session.create_company(canonical_name="Zepto", funding_status="funded")

    result = collect_financials_for_company(db_session.session, "Zepto", financials=ola_like())

    assert result["status"] == "not_applicable"


def test_collected_results_feed_the_financial_features(db_session):
    company = db_session.create_company(canonical_name="Ola Electric", ticker="OLAELEC", exchange="NSE")
    collect_financials_for_company(db_session.session, "Ola Electric", financials=ola_like())

    inputs = gather_inputs(db_session.session, company, datetime.now(UTC))

    assert inputs.values["revenue_growth_yoy"] == -48.5
    assert inputs.values["operating_margin"] == -104.0
    assert inputs.values["cash_runway_months"] == 16.0
    assert inputs.values["debt_to_equity"] == 0.6


def test_the_scorecard_says_when_results_are_in_and_what_stands_out():
    config = load_config()
    pillar = {"score": 70.0, "coverage": 1.0, "weight": 0.2}

    with_results = pillar_row("Financial", pillar, {"inputs": {"operating_margin": 5.0}}, config, listed=True)
    price_only = pillar_row("Financial", {**pillar, "coverage": 0.15}, {"inputs": {}}, config, listed=True)

    assert with_results["note"] == "Financial results and share price"
    assert price_only["note"] == "Share price only · 15% of its inputs"
    assert results_sentences({"revenue_growth_yoy": -48.5, "operating_margin": -104.0, "cash_runway_months": 16.0}) == [
        "Revenue is down 48% on a year earlier.",
        "The business is spending more cash than it brings in, with about 16 months of cash left.",
    ]
    assert results_sentences({"revenue_growth_yoy": 4.0, "operating_margin": 20.0, "cash_runway_months": 60.0}) == []


def test_the_source_is_registered_under_its_name():
    assert FINANCIALS_SOURCE_NAME == "yahoo_finance_fundamentals"
