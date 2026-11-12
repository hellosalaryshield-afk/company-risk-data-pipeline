from datetime import UTC, datetime, timedelta

import pytest

from app.config.settings import Settings
from app.models.collection import CollectionRun
from app.pipeline import freshness
from app.pipeline.freshness import FRESH, MISSING, STALE, check_source, refresh_company
from app.pipeline.source_selection import MARKET_SOURCE, MCA_SOURCE, NEWS_SIGNALS_SOURCE, WORKPLACE_SOURCE

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def add_run(db_session, company, source, hours_ago, status="completed"):
    when = NOW - timedelta(hours=hours_ago)
    db_session.session.add(
        CollectionRun(company_id=company.id, status=status, started_at=when, completed_at=when, extra_metadata={"source": source})
    )
    db_session.session.flush()


@pytest.fixture
def fetched(monkeypatch):
    """Record which sources would be fetched instead of calling them."""
    calls = []

    def fake_run_source(db, source_name, **kwargs):
        calls.append(source_name)
        return {"source": source_name, "status": "completed"}

    monkeypatch.setattr(freshness, "run_source", fake_run_source)
    return calls


@pytest.fixture
def listed(db_session):
    return db_session.create_company(canonical_name="Swiggy", ticker="SWIGGY", exchange="NSE")


def test_fresh_stale_and_missing(db_session, listed):
    add_run(db_session, listed, MARKET_SOURCE, hours_ago=3)
    add_run(db_session, listed, MCA_SOURCE, hours_ago=24 * 40)

    assert check_source(db_session.session, listed.id, MARKET_SOURCE, NOW)["state"] == FRESH
    stale = check_source(db_session.session, listed.id, MCA_SOURCE, NOW)
    assert stale["state"] == STALE and stale["age_days"] == 40.0 and stale["max_age_days"] == 30
    assert check_source(db_session.session, listed.id, NEWS_SIGNALS_SOURCE, NOW)["state"] == MISSING


def test_a_failed_run_is_not_fresh_data(db_session, listed):
    add_run(db_session, listed, MARKET_SOURCE, hours_ago=1, status="failed")

    assert check_source(db_session.session, listed.id, MARKET_SOURCE, NOW)["state"] == MISSING


def test_only_stale_or_missing_sources_are_fetched(db_session, listed, fetched):
    add_run(db_session, listed, MARKET_SOURCE, hours_ago=3)  # fresh
    add_run(db_session, listed, MCA_SOURCE, hours_ago=24 * 10)  # within 30 days, fresh
    add_run(db_session, listed, NEWS_SIGNALS_SOURCE, hours_ago=30)  # older than 1 day, stale

    rows = {r["source"]: r for r in refresh_company(db_session.session, listed, Settings(), now=NOW)}

    assert fetched == [NEWS_SIGNALS_SOURCE]
    assert rows[MARKET_SOURCE]["action"] == "used stored data"
    assert rows[MCA_SOURCE]["action"] == "used stored data"
    assert rows[NEWS_SIGNALS_SOURCE]["state"] == STALE and rows[NEWS_SIGNALS_SOURCE]["action"] == "fetched"


def test_paid_sources_are_fetched_only_when_asked(db_session, listed, fetched):
    for source in (MARKET_SOURCE, MCA_SOURCE, NEWS_SIGNALS_SOURCE):
        add_run(db_session, listed, source, hours_ago=1)

    rows = {r["source"]: r for r in refresh_company(db_session.session, listed, Settings(), now=NOW)}
    assert fetched == [] and rows[WORKPLACE_SOURCE]["action"] == "not fetched: paid source"

    refresh_company(db_session.session, listed, Settings(), now=NOW, include_metered_sources=True)
    assert fetched == [WORKPLACE_SOURCE]


def test_a_source_that_just_failed_is_not_retried_on_every_report(db_session, listed, fetched):
    for source in (MARKET_SOURCE, NEWS_SIGNALS_SOURCE):
        add_run(db_session, listed, source, hours_ago=1)
    add_run(db_session, listed, MCA_SOURCE, hours_ago=0.25, status="failed")

    rows = {r["source"]: r for r in refresh_company(db_session.session, listed, Settings(), now=NOW)}

    assert MCA_SOURCE not in fetched
    assert rows[MCA_SOURCE]["action"].startswith("not fetched: failed in the last")


def test_market_data_is_not_fetched_for_an_unlisted_company(db_session, fetched):
    startup = db_session.create_company(canonical_name="Kitebird")

    sources = [r["source"] for r in refresh_company(db_session.session, startup, Settings(), now=NOW)]

    assert MARKET_SOURCE not in sources and MARKET_SOURCE not in fetched
