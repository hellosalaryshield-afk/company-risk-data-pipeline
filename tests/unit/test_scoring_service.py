from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.scoring import RiskScore, ScorePeerStat
from app.models.source import DataSource
from app.pipeline.news_signal_collection import CLASSIFIED_RECORD_TYPE, get_or_create_news_signal_source
from app.scoring.config import load_config
from app.scoring.inputs import gather_inputs, news_events
from app.scoring.service import build_peer_tables, load_peer_tables, score_company

AS_OF = datetime(2026, 11, 6, tzinfo=UTC)
# Small test universe: let a handful of companies form a peer group.
CONFIG = {**load_config(), "min_peer_group_size": 2, "min_values_for_percentile": 2}


@pytest.fixture
def market_source(db_session):
    source = DataSource(name="yahoo_finance_chart", source_type="api")
    db_session.session.add(source)
    db_session.session.flush()
    return source


def listed(db_session, name, change, market_source, sector="IT Services", status="Active", incorporated=date(2000, 1, 1)):
    company = db_session.create_company(
        canonical_name=name,
        aliases=[],
        ticker=name[:4].upper(),
        exchange="NSE",
        sector=sector,
        company_status=status,
        incorporation_date=incorporated,
    )
    db_session.session.add(
        KpiObservation(
            company_id=company.id,
            source_id=market_source.id,
            kpi_name="market_price_change_pct_period",
            value_numeric=Decimal(str(change)),
            observed_at=AS_OF - timedelta(days=1),
            collected_at=AS_OF - timedelta(days=1),
        )
    )
    db_session.session.flush()
    return company


def news(db_session, company, signal, days_ago, event_id, url, about=True, severity=2, event="An event"):
    source = get_or_create_news_signal_source(db_session.session)
    db_session.session.add(
        SourceRecord(
            company_id=company.id,
            source_id=source.id,
            record_type=CLASSIFIED_RECORD_TYPE,
            url=url,
            title=event,
            published_at=AS_OF - timedelta(days=days_ago),
            raw_data={},
            normalized_data={"about_company": about, "signal": signal, "severity": severity, "event": event, "event_id": event_id},
        )
    )
    db_session.session.flush()


def scanned(db_session, company, days_ago=1):
    db_session.session.add(
        CollectionRun(
            company_id=company.id,
            status="completed",
            started_at=AS_OF - timedelta(days=days_ago),
            extra_metadata={"source": "gemini_news_signals"},
        )
    )
    db_session.session.flush()


# Section 9, test 8 (article side)
def test_three_articles_on_the_same_event_count_once(db_session, market_source):
    company = listed(db_session, "Acme", -10, market_source)
    for i in range(3):
        news(db_session, company, "layoffs", days_ago=3 + i, event_id="evt-1", url=f"https://n.example/{i}", event="Acme cuts 300 jobs")

    events = news_events(db_session.session, company.id, AS_OF)

    assert len(events) == 1
    assert events[0].label == "Acme cuts 300 jobs"
    assert events[0].published_at == (AS_OF - timedelta(days=5))  # dated from the first report


def test_inputs_are_none_when_missing_never_zero(db_session, market_source):
    company = db_session.create_company(canonical_name="Bare Co", aliases=[], sector="IT Services")

    inputs = gather_inputs(db_session.session, company, AS_OF)

    assert inputs.values == {}
    assert inputs.news_scanned_at is None


def test_mca_status_drives_the_filings_overdue_signal(db_session, market_source):
    late = listed(db_session, "Late Co", 0, market_source, status="Inactive for e-filing")
    fine = listed(db_session, "Fine Co", 0, market_source, status="Active")

    assert gather_inputs(db_session.session, late, AS_OF).values["filings_overdue"] == 1.0
    assert gather_inputs(db_session.session, fine, AS_OF).values["filings_overdue"] == 0.0


def test_months_since_funding_comes_from_funding_news_for_unlisted_companies(db_session):
    startup = db_session.create_company(canonical_name="Startup", aliases=[], sector="Fintech")
    news(db_session, startup, "funding_raised", days_ago=61, event_id="f1", url="https://n.example/f1")

    inputs = gather_inputs(db_session.session, startup, AS_OF)

    assert inputs.listed is False
    assert inputs.values["months_since_last_funding"] == pytest.approx(2.0, abs=0.1)


def test_a_company_without_a_recent_news_scan_has_no_news_pillar(db_session, market_source):
    target = listed(db_session, "A", -40, market_source)
    listed(db_session, "B", 5, market_source)
    listed(db_session, "C", 12, market_source)
    tables = build_peer_tables(db_session.session, AS_OF, CONFIG, persist=False)

    result = score_company(db_session.session, target, AS_OF, CONFIG, tables)

    news_pillar = next(p for p in result["pillars"] if p["name"] == "News")
    assert news_pillar["score"] is None and news_pillar["coverage"] == 0
    assert result["status"] == "not_enough_data"  # share price + age alone stay under 0.30


def test_scoring_end_to_end_with_news(db_session, market_source):
    companies = {name: listed(db_session, name, change, market_source) for name, change in (("A", -40), ("B", 5), ("C", 12), ("D", -3))}
    for company in companies.values():
        scanned(db_session, company)
    news(db_session, companies["A"], "layoffs", days_ago=10, event_id="a1", url="https://n.example/a1", event="A lays off 500 staff")
    news(db_session, companies["A"], "leadership_exit", days_ago=30, event_id="a2", url="https://n.example/a2", event="A's CFO resigns")
    news(db_session, companies["B"], "funding_raised", days_ago=5, event_id="b1", url="https://n.example/b1")

    build_peer_tables(db_session.session, AS_OF, CONFIG, persist=True)
    tables = load_peer_tables(db_session.session, CONFIG)
    result = score_company(db_session.session, companies["A"], AS_OF, CONFIG, tables, persist=True)

    assert result["status"] == "scored"
    assert 0 <= result["score"] <= 100
    assert result["band"] in {"Low", "Moderate", "Elevated", "High"}
    assert result["confidence"] == "Limited data"  # no sentiment, thin financials
    assert result["weights_version"] == CONFIG["weights_version"]
    labels = [d["label"] for d in result["drivers"]]
    assert "A lays off 500 staff" in labels
    assert result["drivers"][0]["points"] > 0
    assert db_session.session.query(ScorePeerStat).count() > 0
    stored = db_session.session.query(RiskScore).one()
    assert stored.result["score"] == result["score"]
    assert stored.result["inputs"]["share_price_change_6m"] == -40.0


def test_a_company_with_bad_news_scores_above_one_without(db_session, market_source):
    a = listed(db_session, "A", -10, market_source)
    b = listed(db_session, "B", -10, market_source)
    c = listed(db_session, "C", -10, market_source)
    for company in (a, b, c):
        scanned(db_session, company)
    news(db_session, a, "layoffs", days_ago=3, event_id="a1", url="https://n.example/a1")

    tables = build_peer_tables(db_session.session, AS_OF, CONFIG, persist=False)

    assert score_company(db_session.session, a, AS_OF, CONFIG, tables)["score"] > score_company(db_session.session, b, AS_OF, CONFIG, tables)["score"]


def test_stale_data_halves_the_pillar_coverage(db_session, market_source):
    companies = [listed(db_session, n, ch, market_source) for n, ch in (("A", -40), ("B", 5), ("C", 12))]
    for company in companies:
        scanned(db_session, company)
        # Re-date the share-price reading to 120 days old.
        kpi = db_session.session.query(KpiObservation).filter_by(company_id=company.id).one()
        kpi.collected_at = AS_OF - timedelta(days=120)
    db_session.session.flush()
    tables = build_peer_tables(db_session.session, AS_OF, CONFIG, persist=False)

    result = score_company(db_session.session, companies[0], AS_OF, CONFIG, tables)

    financial = next(p for p in result["pillars"] if p["name"] == "Financial")
    assert financial["coverage"] == pytest.approx(0.15 / 2, abs=0.01)
