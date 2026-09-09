from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.settings import Settings
from app.pipeline.market_collection import collect_market_for_company
from app.pipeline.mca_collection import McaCollectionError, collect_mca_for_company
from app.pipeline.news_collection import NewsCollectionError, collect_news_for_company
from app.pipeline.source_selection import (
    ALL_SOURCES,
    MARKET_SOURCE,
    MCA_SOURCE,
    NEWS_SOURCE,
    WORKPLACE_SOURCE,
    is_metered,
    metered_skip_reason,
    skip_reason,
    sources_for_segment,
)
from app.pipeline.workplace_collection import WorkplaceCollectionError, collect_workplace_for_company

# Statuses that mean the source ran and produced usable data.
SUCCESS_STATUSES = {"completed"}

# Statuses where the source answered but the result was deliberately not used.
REJECTED_STATUSES = {"match_rejected"}


def collect_company_data(
    db: Session,
    query: str,
    settings: Settings,
    days_back: int = 30,
    page_size: int = 25,
    range_period: str = "6mo",
    include_metered_sources: bool = False,
) -> dict:
    """Run every source that applies to one company and return a combined summary.

    Each source keeps its own collection run, so per-source success and failure stay
    visible in `collection_runs`. A source that fails or is not configured never stops
    the others.
    """
    repository = CompanyRepository(db)
    resolved = CompanyResolver(repository).resolve(query)

    if not resolved.match:
        return {
            "status": resolved.status,
            "query": query,
            "confidence": resolved.confidence,
            "message": (
                "Company could not be resolved. Pick one of the candidates below, "
                "or add the company to the registry first."
            ),
            "candidates": [
                {"id": candidate.id, "canonical_name": candidate.canonical_name}
                for candidate in resolved.candidates
            ],
            "sources": [],
            "kpis": {},
        }

    company = resolved.match
    classification = repository.classify_segment(company)
    db.commit()

    selected = sources_for_segment(classification.segment)
    started_at = datetime.now(UTC)
    results: list[dict] = []

    for source_name in ALL_SOURCES:
        if source_name not in selected:
            results.append(
                {
                    "source": source_name,
                    "status": "skipped",
                    "message": skip_reason(source_name, classification.segment),
                }
            )
            continue

        if is_metered(source_name) and not include_metered_sources:
            results.append(
                {
                    "source": source_name,
                    "status": "skipped",
                    "message": metered_skip_reason(source_name),
                }
            )
            continue

        results.append(
            run_source(
                db=db,
                source_name=source_name,
                company_name=company.canonical_name,
                settings=settings,
                days_back=days_back,
                page_size=page_size,
                range_period=range_period,
            )
        )

    merged_kpis: dict[str, float] = {}
    for result in results:
        merged_kpis.update(result.get("kpis") or {})

    succeeded = [r["source"] for r in results if r["status"] in SUCCESS_STATUSES]
    rejected = [r["source"] for r in results if r["status"] in REJECTED_STATUSES]
    failed = [r["source"] for r in results if r["status"] in {"source_failed", "error"}]
    skipped = [r["source"] for r in results if r["status"] in {"skipped", "not_configured", "not_applicable"}]

    return {
        "status": "completed" if succeeded else "no_sources_succeeded",
        "query": query,
        "confidence": resolved.confidence,
        "company": {
            "id": company.id,
            "canonical_name": company.canonical_name,
            "company_segment": classification.segment,
            "country": company.country,
            "sector": company.sector,
        },
        "segment": {
            "segment": classification.segment,
            "geography": classification.geography,
            "listing_status": classification.listing_status,
            "funding_status": classification.funding_status,
            "confidence": classification.confidence,
            "reasons": classification.reasons,
        },
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "sources": results,
        "sources_succeeded": succeeded,
        "sources_failed": failed,
        "sources_skipped": skipped,
        "sources_match_rejected": rejected,
        "kpis": merged_kpis,
    }


def run_source(
    db: Session,
    source_name: str,
    company_name: str,
    settings: Settings,
    days_back: int,
    page_size: int,
    range_period: str,
) -> dict:
    """Run one source and normalize its outcome into a common shape."""
    try:
        if source_name == NEWS_SOURCE:
            result = collect_news_for_company(
                db=db,
                query=company_name,
                settings=settings,
                days_back=days_back,
                page_size=page_size,
            )
        elif source_name == MCA_SOURCE:
            result = collect_mca_for_company(db=db, query=company_name, settings=settings)
        elif source_name == MARKET_SOURCE:
            result = collect_market_for_company(db=db, query=company_name, range_period=range_period)
        elif source_name == WORKPLACE_SOURCE:
            result = collect_workplace_for_company(db=db, query=company_name, settings=settings)
        else:
            return {"source": source_name, "status": "error", "message": f"Unknown source {source_name}."}
    except (NewsCollectionError, McaCollectionError, WorkplaceCollectionError) as exc:
        db.rollback()
        return {"source": source_name, "status": "not_configured", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop the rest
        db.rollback()
        return {
            "source": source_name,
            "status": "error",
            "message": f"{exc.__class__.__name__}: {exc}",
        }

    return {
        "source": source_name,
        "status": result.get("status", "unknown"),
        "collection_run_id": result.get("collection_run_id"),
        "records_stored": result.get("records_stored"),
        "record_found": result.get("record_found"),
        "match_confidence": result.get("match_confidence"),
        "message": result.get("message"),
        "kpis": result.get("kpis") or {},
    }
