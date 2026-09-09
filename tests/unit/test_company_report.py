from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.collection import KpiObservation
from app.models.source import DataSource
from app.reports.company_report import build_company_report, coverage_band, direction_for, latest_kpis
from app.reports.renderer import render_company_report_html, render_company_report_pdf


def add_source(db_session, name: str) -> DataSource:
    source = DataSource(name=name, source_type="api", requires_auth=False)
    db_session.session.add(source)
    db_session.session.flush()
    return source


def add_kpi(db_session, company, source, name, value, unit, days_ago=0):
    observed = datetime.now(UTC) - timedelta(days=days_ago)
    db_session.session.add(
        KpiObservation(
            company_id=company.id,
            source_id=source.id,
            kpi_name=name,
            value_numeric=Decimal(str(value)),
            unit=unit,
            observed_at=observed,
        )
    )
    db_session.session.flush()


def test_direction_for_compares_against_the_previous_reading():
    assert direction_for(5.0, 3.0) == "up"
    assert direction_for(3.0, 5.0) == "down"
    assert direction_for(3.0, 3.0) == "flat"
    assert direction_for(3.0, None) == "no_history"


def test_coverage_band_labels_ratios():
    assert coverage_band(3, 3)["band"] == "Good"
    assert coverage_band(3, 2)["band"] == "Partial"
    assert coverage_band(3, 0)["band"] == "Thin"
    assert coverage_band(0, 0)["band"] == "Thin"


def test_latest_kpis_returns_newest_value_with_previous_for_trend(db_session):
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY Ltd"])
    source = add_source(db_session, "newsapi")
    add_kpi(db_session, company, source, "layoff_news_count", 1, "count", days_ago=10)
    add_kpi(db_session, company, source, "layoff_news_count", 4, "count", days_ago=0)

    kpis = latest_kpis(db_session.session, company.id)
    layoffs = next(kpi for kpi in kpis if kpi["kpi_name"] == "layoff_news_count")

    assert layoffs["value"] == pytest.approx(4.0)
    assert layoffs["previous_value"] == pytest.approx(1.0)
    assert layoffs["direction"] == "up"
    assert layoffs["observation_count"] == 2
    assert layoffs["label"] == "Articles mentioning layoffs"
    assert layoffs["higher_is_riskier"] is True


def test_build_company_report_for_a_listed_company(db_session):
    company = db_session.create_company(
        canonical_name="Tata Consultancy Services",
        aliases=["TCS"],
        ticker="TCS",
        exchange="NSE",
    )
    source = add_source(db_session, "yahoo_finance_chart")
    add_kpi(db_session, company, source, "market_drawdown_from_52w_high_pct", 32.67, "percent")
    add_kpi(db_session, company, source, "market_annualized_volatility_pct", 32.46, "percent")

    report = build_company_report(db_session.session, company)

    assert report["segment"]["segment"] == "INDIA_LISTED"
    assert set(report["applicable_sources"]) == {
        "newsapi",
        "data_gov_mca_company_master",
        "yahoo_finance_chart",
        "apify_glassdoor_company_search",
    }
    assert len(report["kpis"]) == 2
    assert any("32.7% below its 52-week high" in note for note in report["notes"])
    assert report["macro"] == []


def test_report_explains_an_undetermined_segment(db_session):
    company = db_session.create_company(canonical_name="Zepto", aliases=["Kiranakart"])

    report = build_company_report(db_session.session, company)

    assert report["segment"]["segment"] is None
    assert report["coverage"]["band"] == "Thin"
    assert any("funding status is not recorded" in note for note in report["notes"])


def test_html_report_renders_and_never_claims_a_risk_score(db_session):
    company = db_session.create_company(canonical_name="Razorpay", aliases=["Razorpay Software"], funding_status="funded")
    source = add_source(db_session, "newsapi")
    add_kpi(db_session, company, source, "news_article_count", 4, "count")
    add_kpi(db_session, company, source, "layoff_news_count", 0, "count")

    report = build_company_report(db_session.session, company)
    html = render_company_report_html(report, footer_note="Contact note here.")

    assert "Razorpay" in html
    assert "INDIA_UNLISTED_FUNDED" in html
    assert "not a risk score" in html
    assert "Contact note here." in html
    assert "Articles mentioning layoffs" in html


def test_html_report_escapes_company_names(db_session):
    company = db_session.create_company(canonical_name="Acme <script>alert(1)</script>", aliases=["Acme Evil"])

    html = render_company_report_html(build_company_report(db_session.session, company))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_pdf_report_renders_to_a_pdf_document(db_session):
    company = db_session.create_company(canonical_name="Nykaa", aliases=["FSN E-Commerce"], ticker="NYKAA", exchange="NSE")
    source = add_source(db_session, "yahoo_finance_chart")
    add_kpi(db_session, company, source, "market_price", 341.0, "INR")

    pdf = render_company_report_pdf(build_company_report(db_session.session, company))

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000
