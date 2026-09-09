from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.sources.gdelt import GdeltClient, GdeltFetchResult, GdeltRateLimitError
from app.sources.gdelt_signals import extract_gdelt_kpis, timeline_stats

GDELT_SOURCE_NAME = "gdelt_doc"

MISSING_RESULT = object()

UNITS = {
    "gdelt_avg_tone": "tone",
    "gdelt_worst_day_tone": "tone",
    "gdelt_tone_decline": "tone",
    "gdelt_tone_days": "count",
    "gdelt_negative_day_pct": "percent",
    "gdelt_volume_mean_pct": "percent",
    "gdelt_volume_spike_ratio": "ratio",
    "gdelt_article_count": "count",
}


class GdeltCollectionError(RuntimeError):
    pass


def get_or_create_gdelt_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == GDELT_SOURCE_NAME)).first()
    if source:
        return source

    source = DataSource(
        name=GDELT_SOURCE_NAME,
        source_type="api",
        base_url="https://api.gdeltproject.org",
        description=(
            "GDELT DOC 2.0 API. Supplies daily news tone (sentiment), coverage volume, and "
            "article listings. No API key and no licence restriction on production use."
        ),
        requires_auth=False,
        known_limitations=(
            "Rate limited to roughly one request every five seconds per client, and it answers "
            "with a plain-text notice rather than an error when exceeded. Measured on 2026-09-09: "
            "even six-second spacing drew 429s after a burst. Suitable for scheduled batch "
            "collection, not for a high-traffic synchronous endpoint. Tone is computed over all "
            "matching global coverage, so a common company name can pull in unrelated articles."
        ),
    )
    db.add(source)
    db.flush()
    return source


def collect_gdelt_for_company(
    db: Session,
    query: str,
    timespan: str = "3m",
    max_articles: int = 25,
    fetch_result: GdeltFetchResult | None | object = MISSING_RESULT,
) -> dict:
    resolver = CompanyResolver(CompanyRepository(db))
    resolved = resolver.resolve(query)
    if not resolved.match:
        return {
            "status": resolved.status,
            "query": query,
            "confidence": resolved.confidence,
            "message": "Company could not be resolved. Pick a candidate or add it to the registry first.",
            "candidates": [candidate.canonical_name for candidate in resolved.candidates],
            "kpis": {},
        }

    company = resolved.match

    if fetch_result is MISSING_RESULT:
        try:
            fetch_result = GdeltClient().fetch_company_coverage(
                company.canonical_name,
                timespan=timespan,
                max_articles=max_articles,
            )
        except GdeltRateLimitError as exc:
            return record_gdelt_failure(
                db=db,
                company=company,
                query=query,
                error_message=(
                    f"GDELT rate limit reached: {exc}. Retry with wider spacing or run the "
                    "scheduled batch collector instead."
                ),
            )
        except httpx.TimeoutException:
            return record_gdelt_failure(
                db=db, company=company, query=query, error_message="GDELT request timed out."
            )
        except httpx.HTTPError as exc:
            return record_gdelt_failure(
                db=db,
                company=company,
                query=query,
                error_message=f"GDELT request failed: {exc.__class__.__name__}.",
            )

    source = get_or_create_gdelt_source(db)
    run = CollectionRun(
        company_id=company.id,
        status="running",
        extra_metadata={"source": GDELT_SOURCE_NAME, "query": query, "timespan": timespan},
    )
    db.add(run)
    db.flush()

    if fetch_result is None:
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.extra_metadata = {**run.extra_metadata, "found": False}
        db.commit()
        return {
            "status": "completed",
            "company": company_payload(company),
            "collection_run_id": run.id,
            "source": GDELT_SOURCE_NAME,
            "record_found": False,
            "kpis": {},
        }

    kpis = extract_gdelt_kpis(fetch_result)
    normalized = {
        "company_name": fetch_result.company_name,
        "timespan": fetch_result.timespan,
        "tone": timeline_stats(fetch_result.tone),
        "volume": timeline_stats(fetch_result.volume),
        "article_count": len(fetch_result.articles),
        "kpis": kpis,
    }

    observed_at = latest_observation_time(fetch_result)

    db.add(
        SourceRecord(
            company_id=company.id,
            source_id=source.id,
            collection_run_id=run.id,
            record_type="gdelt_coverage",
            external_id=f"{company.id}:{fetch_result.timespan}",
            title=f"GDELT coverage for {company.canonical_name}",
            observed_at=observed_at,
            published_at=observed_at,
            confidence="medium",
            raw_data=fetch_result.raw_data,
            normalized_data=normalized,
        )
    )

    for kpi_name, value in kpis.items():
        db.add(
            KpiObservation(
                company_id=company.id,
                source_id=source.id,
                kpi_name=kpi_name,
                value_numeric=Decimal(str(value)),
                unit=UNITS.get(kpi_name),
                observed_at=observed_at,
                published_at=observed_at,
                confidence="medium",
                extra_metadata={
                    "source": GDELT_SOURCE_NAME,
                    "query": company.canonical_name,
                    "timespan": fetch_result.timespan,
                },
            )
        )

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.extra_metadata = {**run.extra_metadata, "found": True, "kpi_count": len(kpis)}
    db.commit()

    return {
        "status": "completed",
        "company": company_payload(company),
        "collection_run_id": run.id,
        "source": GDELT_SOURCE_NAME,
        "record_found": True,
        "records_stored": 1,
        "gdelt_record": normalized,
        "kpis": kpis,
    }


def latest_observation_time(result: GdeltFetchResult) -> datetime:
    """Use the last point GDELT actually reported, not the moment we asked."""
    for timeline in (result.tone, result.volume):
        if timeline and timeline.dates:
            return timeline.dates[-1]
    return datetime.now(UTC)


def company_payload(company: Company) -> dict:
    return {"id": company.id, "canonical_name": company.canonical_name}


def record_gdelt_failure(db: Session, company: Company, query: str, error_message: str) -> dict:
    source = get_or_create_gdelt_source(db)
    run = CollectionRun(
        company_id=company.id,
        status="failed",
        completed_at=datetime.now(UTC),
        error_message=error_message,
        extra_metadata={
            "source": GDELT_SOURCE_NAME,
            "query": query,
            "found": False,
            "source_status": "rate_limited",
        },
    )
    db.add(run)
    db.commit()

    return {
        "status": "source_failed",
        "company": company_payload(company),
        "collection_run_id": run.id,
        "source": GDELT_SOURCE_NAME,
        "record_found": False,
        "message": error_message,
        "kpis": {},
    }
