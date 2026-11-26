"""Use stored data while it is fresh; fetch again only when it is stale or missing.

Before a scorecard is built, each source it reads is checked in `collection_runs`:

    fresh    a run finished within the source's limit  -> use the stored data as it is
    stale    the last finished run is older than that  -> fetch again
    missing  the source never finished a run           -> fetch

A source that answered "no record for this company" still counts as checked, so a company
without, say, a Glassdoor page is not looked up again on every report. A source whose last
attempt failed is not retried for `FAILED_RETRY_MINUTES`, so a broken API is not hit on
every request.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.config.settings import Settings
from app.models.collection import CollectionRun
from app.models.company import Company
from app.pipeline.company_collection import SUCCESS_STATUSES, run_source
from app.pipeline.news_feed_collection import collect_news_feeds_if_stale
from app.pipeline.source_selection import (
    FINANCIALS_SOURCE,
    MARKET_SOURCE,
    MCA_SOURCE,
    NEWS_SIGNALS_SOURCE,
    WORKPLACE_SOURCE,
    is_metered,
    sources_for_segment,
)

# How old each source's data may be before the scorecard fetches it again. Share prices and
# news move daily; registry details and employee reviews change slowly.
MAX_AGE_DAYS: dict[str, float] = {
    MARKET_SOURCE: 1,
    # Results come out every quarter; checking monthly picks up a new quarter within weeks.
    FINANCIALS_SOURCE: 30,
    NEWS_SIGNALS_SOURCE: 1,
    MCA_SOURCE: 30,
    WORKPLACE_SOURCE: 90,
}
# The sources the scorecard reads, in the order they are checked.
SCORECARD_SOURCES = tuple(MAX_AGE_DAYS)
FAILED_RETRY_MINUTES = 60
logger = logging.getLogger(__name__)

FRESH, STALE, MISSING = "fresh", "stale", "missing"


def last_run(db: Session, company_id: int, source_name: str, completed: bool) -> CollectionRun | None:
    query = select(CollectionRun).where(
        CollectionRun.company_id == company_id,
        CollectionRun.extra_metadata["source"].as_string() == source_name,
    )
    if completed:
        query = query.where(CollectionRun.status.in_(SUCCESS_STATUSES))
    return db.scalars(query.order_by(desc(CollectionRun.started_at)).limit(1)).first()


def run_time(run: CollectionRun) -> datetime:
    when = run.completed_at or run.started_at
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def check_source(db: Session, company_id: int, source_name: str, now: datetime) -> dict[str, Any]:
    """Say whether one source's stored data for a company is fresh, stale or missing."""
    max_age = MAX_AGE_DAYS[source_name]
    run = last_run(db, company_id, source_name, completed=True)
    if run is None:
        return {"source": source_name, "state": MISSING, "checked_at": None, "age_days": None, "max_age_days": max_age}

    checked_at = run_time(run)
    age_days = (now - checked_at).total_seconds() / 86400
    return {
        "source": source_name,
        "state": FRESH if age_days <= max_age else STALE,
        "checked_at": checked_at.isoformat(),
        "age_days": round(age_days, 1),
        "max_age_days": max_age,
    }


def failed_recently(db: Session, company_id: int, source_name: str, now: datetime) -> bool:
    run = last_run(db, company_id, source_name, completed=False)
    if run is None or run.status in SUCCESS_STATUSES:
        return False
    return now - run_time(run) < timedelta(minutes=FAILED_RETRY_MINUTES)


def refresh_company(
    db: Session,
    company: Company,
    settings: Settings,
    now: datetime | None = None,
    include_metered_sources: bool = False,
) -> list[dict[str, Any]]:
    """Fetch every scorecard source that is stale or missing for one company.

    Returns one row per source with its state before the check and what was done:
    "used stored data", "fetched", "fetch failed", or why it was not fetched.
    """
    now = now or datetime.now(UTC)
    segment = CompanyRepository(db).classify_segment(company).segment
    db.commit()
    applicable = sources_for_segment(segment)

    rows = []
    for source_name in SCORECARD_SOURCES:
        if source_name not in applicable:
            continue
        row = check_source(db, company.id, source_name, now)
        if row["state"] == FRESH:
            row["action"] = "used stored data"
        elif is_metered(source_name) and not include_metered_sources:
            row["action"] = "not fetched: paid source"
        elif failed_recently(db, company.id, source_name, now):
            row["action"] = f"not fetched: failed in the last {FAILED_RETRY_MINUTES} minutes"
        else:
            if source_name == NEWS_SIGNALS_SOURCE:
                # News is found in the stored feeds first. Topping them up is a nicety: if it
                # fails, the check carries on with the feeds already stored.
                try:
                    collect_news_feeds_if_stale(db)
                except Exception:  # noqa: BLE001 - a feed problem must never break a report
                    db.rollback()
                    logger.exception("News feed top-up failed; using the feeds already stored.")
            result = run_source(
                db=db,
                source_name=source_name,
                company_name=company.canonical_name,
                settings=settings,
                days_back=30,
                page_size=25,
                range_period="6mo",
            )
            row["action"] = "fetched" if result["status"] in SUCCESS_STATUSES else "fetch failed"
            row["result_status"] = result["status"]
            if result.get("message"):
                row["message"] = result["message"]
        rows.append(row)
    return rows
