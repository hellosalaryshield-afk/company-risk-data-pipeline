from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.features.point_in_time import (
    evaluate_observation,
    feature_vector_as_of,
    kpi_as_of,
    leakage_scan,
)
from app.models.collection import KpiObservation
from app.models.source import DataSource

AS_OF = datetime(2026, 6, 1, tzinfo=UTC)
BEFORE = datetime(2026, 5, 1, tzinfo=UTC)
AFTER = datetime(2026, 7, 1, tzinfo=UTC)


def add_source(db_session, name: str) -> DataSource:
    source = db_session.session.query(DataSource).filter_by(name=name).first()
    if source:
        return source
    source = DataSource(name=name, source_type="api", requires_auth=False)
    db_session.session.add(source)
    db_session.session.flush()
    return source


def add_kpi(db_session, company, source, name, value, published_at=None, collected_at=None, period_end=None):
    observation = KpiObservation(
        company_id=company.id,
        source_id=source.id,
        kpi_name=name,
        value_numeric=Decimal(str(value)),
        unit="count",
        observed_at=published_at,
        published_at=published_at,
        period_end=period_end,
    )
    db_session.session.add(observation)
    db_session.session.flush()
    if collected_at is not None:
        observation.collected_at = collected_at
        db_session.session.flush()
    return observation


def evaluate(**kwargs):
    defaults = dict(
        kpi_name="x",
        value=1.0,
        source_name="newsapi",
        published_at=BEFORE,
        collected_at=BEFORE,
        period_start=None,
        period_end=None,
        as_of=AS_OF,
    )
    return evaluate_observation(**{**defaults, **kwargs})


def test_a_value_published_before_the_date_and_collected_before_it_is_safe():
    assert evaluate().point_in_time_safe is True


def test_a_missing_publication_date_is_not_verifiable():
    observation = evaluate(published_at=None)

    assert observation.point_in_time_safe is False
    assert any("no publication date" in issue for issue in observation.issues)


def test_a_value_published_after_the_date_is_flagged():
    observation = evaluate(published_at=AFTER)

    assert observation.point_in_time_safe is False
    assert "published after the prediction date" in observation.issues


def test_a_window_running_past_the_date_is_flagged():
    """A count over Mar-Sep contains the future even if it was published in May."""
    observation = evaluate(period_end=AFTER)

    assert observation.point_in_time_safe is False
    assert any("window extends past" in issue for issue in observation.issues)


def test_backfill_from_a_non_revising_source_is_a_warning_not_a_blocker():
    """Every value we hold was collected after any past date.

    If backfill alone disqualified a value, every historical feature vector would be empty
    and backtesting would be impossible. For a source that never restates, today's copy is
    what would have been seen then, so it is recorded as a warning and still usable.
    """
    observation = evaluate(collected_at=AFTER)

    assert observation.point_in_time_safe is True
    assert observation.issues == []
    assert any("does not revise" in warning for warning in observation.warnings)
    assert observation.backfilled_at_as_of is True


def test_a_backfilled_value_is_still_marked_as_backfilled_in_provenance():
    observation = evaluate(collected_at=AFTER)

    assert observation.backfilled_at_as_of is True


def test_backfill_from_a_revising_source_is_called_out_separately():
    observation = evaluate(source_name="data_gov_mca_company_master", collected_at=AFTER)

    assert any("revises earlier figures" in issue for issue in observation.issues)


def test_an_undated_snapshot_source_can_never_be_proven_safe():
    observation = evaluate(source_name="apify_glassdoor_company_search", published_at=BEFORE)

    assert observation.point_in_time_safe is False
    assert any("undated snapshot" in issue for issue in observation.issues)


def test_kpi_as_of_excludes_values_published_after_the_date(db_session):
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY"])
    source = add_source(db_session, "newsapi")
    add_kpi(db_session, company, source, "layoff_news_count", 1, published_at=BEFORE, collected_at=BEFORE)
    add_kpi(db_session, company, source, "future_only_kpi", 9, published_at=AFTER, collected_at=AFTER)

    names = {obs.kpi_name for obs in kpi_as_of(db_session.session, company.id, AS_OF)}

    assert "layoff_news_count" in names
    assert "future_only_kpi" not in names


def test_kpi_as_of_takes_the_newest_value_that_was_already_published(db_session):
    """Asking about June must not return the September revision of the same KPI."""
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY"])
    source = add_source(db_session, "newsapi")
    add_kpi(db_session, company, source, "layoff_news_count", 1, published_at=datetime(2026, 3, 1, tzinfo=UTC), collected_at=BEFORE)
    add_kpi(db_session, company, source, "layoff_news_count", 5, published_at=BEFORE, collected_at=BEFORE)
    add_kpi(db_session, company, source, "layoff_news_count", 99, published_at=AFTER, collected_at=AFTER)

    observations = kpi_as_of(db_session.session, company.id, AS_OF)
    layoffs = next(obs for obs in observations if obs.kpi_name == "layoff_news_count")

    assert layoffs.value == pytest.approx(5.0)


def test_feature_vector_drops_unsafe_values_and_says_why(db_session):
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY"])
    news = add_source(db_session, "newsapi")
    glassdoor = add_source(db_session, "apify_glassdoor_company_search")
    add_kpi(db_session, company, news, "layoff_news_count", 3, published_at=BEFORE, collected_at=BEFORE)
    add_kpi(db_session, company, glassdoor, "glassdoor_rating", 4.1, published_at=BEFORE, collected_at=BEFORE)

    vector = feature_vector_as_of(db_session.session, company.id, AS_OF)

    assert vector["features"] == {"layoff_news_count": pytest.approx(3.0)}
    excluded = {item["kpi_name"] for item in vector["excluded"]}
    assert excluded == {"glassdoor_rating"}
    assert vector["provenance"][0]["source"] == "newsapi"


def test_feature_vector_can_include_unsafe_values_for_inspection(db_session):
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY"])
    glassdoor = add_source(db_session, "apify_glassdoor_company_search")
    add_kpi(db_session, company, glassdoor, "glassdoor_rating", 4.1, published_at=BEFORE, collected_at=BEFORE)

    vector = feature_vector_as_of(db_session.session, company.id, AS_OF, safe_only=False)

    assert "glassdoor_rating" in vector["features"]
    assert vector["excluded"] == []


def test_leakage_scan_reports_impossible_timestamps(db_session):
    """Collected before published means the timestamps themselves are wrong."""
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY"])
    source = add_source(db_session, "newsapi")
    add_kpi(
        db_session, company, source, "layoff_news_count", 1,
        published_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        collected_at=datetime(2026, 5, 1, 11, 0, tzinfo=UTC),
    )

    report = leakage_scan(db_session.session, as_of=AS_OF)

    assert len(report["impossible_timestamps"]) == 1
    assert report["impossible_timestamps"][0]["kpi_name"] == "layoff_news_count"


def test_leakage_scan_groups_findings_by_issue(db_session):
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY"])
    glassdoor = add_source(db_session, "apify_glassdoor_company_search")
    for name in ("glassdoor_rating", "glassdoor_ceo_rating"):
        add_kpi(db_session, company, glassdoor, name, 4.0, published_at=None, collected_at=BEFORE)

    report = leakage_scan(db_session.session, as_of=AS_OF)

    assert report["observations_checked"] == 2
    assert report["point_in_time_safe"] == 0
    issues = {finding["issue"] for finding in report["findings"]}
    assert any("no publication date" in issue for issue in issues)
    assert any("undated snapshot" in issue for issue in issues)


def test_leakage_scan_passes_clean_data(db_session):
    company = db_session.create_company(canonical_name="Infosys", aliases=["INFY"])
    source = add_source(db_session, "newsapi")
    add_kpi(db_session, company, source, "layoff_news_count", 1, published_at=BEFORE, collected_at=BEFORE)

    report = leakage_scan(db_session.session, as_of=AS_OF)

    assert report["with_issues"] == 0
    assert report["findings"] == []
    assert report["impossible_timestamps"] == []
