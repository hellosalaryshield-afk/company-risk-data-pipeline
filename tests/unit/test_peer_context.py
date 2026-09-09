from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.collection import KpiObservation
from app.models.source import DataSource
from app.reports.company_report import build_company_report
from app.reports.peer_context import build_peer_context
from app.reports.renderer import render_company_report_html


def add_source(db_session, name: str = "yahoo_finance_chart") -> DataSource:
    source = DataSource(name=name, source_type="api", requires_auth=False)
    db_session.session.add(source)
    db_session.session.flush()
    return source


def add_kpi(db_session, company, source, name, value, days_ago=0):
    db_session.session.add(
        KpiObservation(
            company_id=company.id,
            source_id=source.id,
            kpi_name=name,
            value_numeric=Decimal(str(value)),
            unit="percent",
            observed_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    db_session.session.flush()


def make_listed(db_session, name, sector="IT Services"):
    company = db_session.create_company(canonical_name=name, aliases=[f"{name} Ltd"], ticker=name[:6].upper(), exchange="NSE")
    company.sector = sector
    db_session.session.flush()
    return company


def test_cohort_median_is_returned_for_a_company_with_no_data(db_session):
    source = add_source(db_session)
    subject = make_listed(db_session, "Infosys")
    for name, value in [("Wipro", 10.0), ("HCLTech", 20.0), ("Mindtree", 30.0)]:
        add_kpi(db_session, make_listed(db_session, name), source, "market_drawdown_from_52w_high_pct", value)

    context = build_peer_context(db_session.session, subject, "INDIA_LISTED", own_kpi_names=set())

    assert context["cohort"] == "sector: IT Services"
    assert context["peer_count"] == 3
    estimate = next(e for e in context["estimates"] if e["kpi_name"] == "market_drawdown_from_52w_high_pct")
    assert estimate["median"] == pytest.approx(20.0)
    assert estimate["peer_count"] == 3


def test_a_companys_own_value_is_never_replaced_by_a_cohort_estimate(db_session):
    source = add_source(db_session)
    subject = make_listed(db_session, "Infosys")
    for name in ("Wipro", "HCLTech"):
        add_kpi(db_session, make_listed(db_session, name), source, "market_price", 100.0)

    context = build_peer_context(
        db_session.session, subject, "INDIA_LISTED", own_kpi_names={"market_price"}
    )

    assert all(e["kpi_name"] != "market_price" for e in context["estimates"])


def test_cohort_widens_when_the_tightest_group_has_no_data(db_session):
    """A sector can hold peers that have never been collected. Widen rather than give up."""
    source = add_source(db_session)
    subject = make_listed(db_session, "Infosys", sector="IT Services")
    make_listed(db_session, "Wipro", sector="IT Services")  # same sector, no KPIs
    for name in ("Nykaa", "Zomato"):
        add_kpi(db_session, make_listed(db_session, name, sector="E-Commerce"), source, "market_price", 50.0)

    context = build_peer_context(db_session.session, subject, "INDIA_LISTED", own_kpi_names=set())

    assert context["cohort"] != "sector: IT Services"
    assert context["estimates"]


def test_a_single_peer_is_not_enough_for_a_median(db_session):
    source = add_source(db_session)
    subject = make_listed(db_session, "Infosys")
    add_kpi(db_session, make_listed(db_session, "Wipro"), source, "market_price", 100.0)

    context = build_peer_context(db_session.session, subject, "INDIA_LISTED", own_kpi_names=set())

    assert context["estimates"] == []
    assert context["peer_count"] == 0


def test_empty_registry_says_so_instead_of_guessing(db_session):
    subject = make_listed(db_session, "Infosys")

    context = build_peer_context(db_session.session, subject, "INDIA_LISTED", own_kpi_names=set())

    assert context["estimates"] == []
    assert "No comparable company" in context["note"]


def test_only_the_latest_value_per_peer_feeds_the_median(db_session):
    source = add_source(db_session)
    subject = make_listed(db_session, "Infosys")
    for name in ("Wipro", "HCLTech"):
        peer = make_listed(db_session, name)
        add_kpi(db_session, peer, source, "market_price", 999.0, days_ago=30)
        add_kpi(db_session, peer, source, "market_price", 100.0, days_ago=0)

    context = build_peer_context(db_session.session, subject, "INDIA_LISTED", own_kpi_names=set())

    estimate = next(e for e in context["estimates"] if e["kpi_name"] == "market_price")
    assert estimate["median"] == pytest.approx(100.0)


def test_report_shows_the_fallback_and_labels_it_as_not_the_company(db_session):
    source = add_source(db_session)
    subject = make_listed(db_session, "Infosys")
    for name, value in [("Wipro", 10.0), ("HCLTech", 20.0)]:
        add_kpi(db_session, make_listed(db_session, name), source, "market_drawdown_from_52w_high_pct", value)

    report = build_company_report(db_session.session, subject)
    html = render_company_report_html(report)

    assert report["kpis"] == []
    assert report["peer_context"]["estimates"]
    assert any("falls back to median values" in note for note in report["notes"])
    assert "Sector estimate" in html
    assert "not measurements of this company" in html
    # The overclaiming guard must survive the fallback path.
    assert "not a risk score" in html
