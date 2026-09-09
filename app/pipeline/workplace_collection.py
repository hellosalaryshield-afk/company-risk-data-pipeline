from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.settings import Settings
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.sources.glassdoor import (
    COST_PER_RUN_USD,
    DEFAULT_DOMAIN,
    GlassdoorClient,
    match_confidence,
    normalize_glassdoor_record,
)
from app.sources.workplace_signals import extract_workplace_kpis, unit_for

WORKPLACE_SOURCE_NAME = "apify_glassdoor_company_search"

MISSING_RECORD = object()


class WorkplaceCollectionError(RuntimeError):
    pass


def get_or_create_workplace_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == WORKPLACE_SOURCE_NAME)).first()
    if source:
        return source

    source = DataSource(
        name=WORKPLACE_SOURCE_NAME,
        source_type="api",
        base_url="https://api.apify.com",
        description=(
            "Apify burbn/glassdoor-company-search actor. Supplies workplace ratings, review "
            "volume, and open job count. Glassdoor closed its own public API in 2024."
        ),
        requires_auth=True,
        known_limitations=(
            f"Billed per run (about ${COST_PER_RUN_USD:.2f} per company). The actor returns its "
            "top search hit without verifying identity, so matches are scored and a low-confidence "
            "match is stored but never written onto the company record. Ratings are point-in-time "
            "with no history, so velocity needs repeated collection."
        ),
    )
    db.add(source)
    db.flush()
    return source


def collect_workplace_for_company(
    db: Session,
    query: str,
    settings: Settings,
    domain: str = DEFAULT_DOMAIN,
    raw_record: dict[str, Any] | None | object = MISSING_RECORD,
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
        }

    company = resolved.match

    if raw_record is MISSING_RECORD:
        if not settings.apify_token:
            raise WorkplaceCollectionError("APIFY_TOKEN is not configured.")
        try:
            raw_record = GlassdoorClient(settings.apify_token, domain=domain).fetch_company_profile(
                company.canonical_name
            )
        except httpx.TimeoutException:
            return record_workplace_failure(
                db=db,
                company=company,
                query=query,
                error_message="Apify Glassdoor actor timed out. Source is temporarily unavailable.",
            )
        except httpx.HTTPStatusError as exc:
            return record_workplace_failure(
                db=db,
                company=company,
                query=query,
                error_message=f"Apify Glassdoor actor returned HTTP {exc.response.status_code}.",
            )
        except httpx.HTTPError as exc:
            return record_workplace_failure(
                db=db,
                company=company,
                query=query,
                error_message=f"Apify Glassdoor request failed: {exc.__class__.__name__}.",
            )

    source = get_or_create_workplace_source(db)
    run = CollectionRun(
        company_id=company.id,
        status="running",
        extra_metadata={
            "source": WORKPLACE_SOURCE_NAME,
            "query": query,
            "domain": domain,
            "estimated_cost_usd": COST_PER_RUN_USD,
        },
    )
    db.add(run)
    db.flush()

    if not raw_record:
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.extra_metadata = {**run.extra_metadata, "found": False}
        db.commit()
        return {
            "status": "completed",
            "company": company_payload(company),
            "collection_run_id": run.id,
            "source": WORKPLACE_SOURCE_NAME,
            "record_found": False,
            "match_confidence": None,
            "workplace_record": None,
            "kpis": {},
        }

    confidence, match_reason = match_confidence(company.canonical_name, raw_record)
    normalized = normalize_glassdoor_record(raw_record)
    normalized["match_confidence"] = confidence
    normalized["match_reason"] = match_reason

    db.add(
        SourceRecord(
            company_id=company.id,
            source_id=source.id,
            collection_run_id=run.id,
            record_type="glassdoor_company_profile",
            external_id=str(normalized.get("company_id")) if normalized.get("company_id") else None,
            title=normalized.get("name"),
            url=normalized.get("company_link"),
            observed_at=datetime.now(UTC),
            published_at=None,
            confidence=confidence,
            raw_data=raw_record,
            normalized_data=normalized,
        )
    )

    # A weak match is kept for audit but never turned into KPIs, otherwise a different
    # company's ratings would enter the feature set.
    kpis: dict[str, float] = {}
    if confidence == "low":
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.extra_metadata = {
            **run.extra_metadata,
            "found": True,
            "match_confidence": confidence,
            "match_rejected": True,
        }
        db.commit()
        return {
            "status": "match_rejected",
            "company": company_payload(company),
            "collection_run_id": run.id,
            "source": WORKPLACE_SOURCE_NAME,
            "record_found": True,
            "match_confidence": confidence,
            "message": match_reason,
            "workplace_record": normalized,
            "kpis": {},
        }

    kpis = extract_workplace_kpis(normalized)
    observed_at = datetime.now(UTC)
    for kpi_name, value in kpis.items():
        db.add(
            KpiObservation(
                company_id=company.id,
                source_id=source.id,
                kpi_name=kpi_name,
                value_numeric=Decimal(str(value)),
                unit=unit_for(kpi_name),
                observed_at=observed_at,
                published_at=None,
                confidence=confidence,
                extra_metadata={
                    "source": WORKPLACE_SOURCE_NAME,
                    "glassdoor_company_id": normalized.get("company_id"),
                    "match_confidence": confidence,
                },
            )
        )

    if normalized.get("industry") and not company.industry:
        company.industry = normalized["industry"]
    if normalized.get("website") and not company.website:
        company.website = normalized["website"]

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.extra_metadata = {
        **run.extra_metadata,
        "found": True,
        "match_confidence": confidence,
        "kpis_stored": len(kpis),
    }
    db.commit()

    return {
        "status": "completed",
        "company": company_payload(company),
        "collection_run_id": run.id,
        "source": WORKPLACE_SOURCE_NAME,
        "record_found": True,
        "match_confidence": confidence,
        "message": match_reason,
        "workplace_record": normalized,
        "kpis": kpis,
    }


def company_payload(company: Company) -> dict:
    return {"id": company.id, "canonical_name": company.canonical_name}


def record_workplace_failure(db: Session, company: Company, query: str, error_message: str) -> dict:
    source = get_or_create_workplace_source(db)
    run = CollectionRun(
        company_id=company.id,
        status="failed",
        completed_at=datetime.now(UTC),
        error_message=error_message,
        extra_metadata={
            "source": WORKPLACE_SOURCE_NAME,
            "query": query,
            "found": False,
            "source_status": "unavailable",
        },
    )
    db.add(run)
    db.commit()

    return {
        "status": "source_failed",
        "company": company_payload(company),
        "collection_run_id": run.id,
        "source": WORKPLACE_SOURCE_NAME,
        "record_found": False,
        "match_confidence": None,
        "workplace_record": None,
        "message": error_message,
        "kpis": {},
    }
