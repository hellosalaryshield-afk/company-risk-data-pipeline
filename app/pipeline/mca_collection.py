from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.settings import Settings
from app.models.collection import CollectionRun, SourceRecord
from app.models.source import DataSource
from app.sources.data_gov_mca import DataGovMcaClient, normalize_mca_record, parse_mca_date

MCA_SOURCE_NAME = "data_gov_mca_company_master"


class McaCollectionError(RuntimeError):
    pass


MISSING_RECORD = object()


def get_or_create_mca_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == MCA_SOURCE_NAME)).first()
    if source:
        return source

    source = DataSource(
        name=MCA_SOURCE_NAME,
        source_type="api",
        base_url="https://api.data.gov.in",
        description="Data.gov.in MCA Company Master Data API.",
        requires_auth=True,
        known_limitations="Exact-name matching can miss aliases; data freshness depends on Data.gov.in publication schedule.",
    )
    db.add(source)
    db.flush()
    return source


def collect_mca_for_company(
    db: Session,
    query: str,
    settings: Settings,
    raw_record: dict | None | object = MISSING_RECORD,
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

    if raw_record is MISSING_RECORD:
        if not settings.data_gov_api_key:
            raise McaCollectionError("DATA_GOV_API_KEY is not configured.")
        try:
            raw_record = DataGovMcaClient(settings.data_gov_api_key).fetch_company_master_data(
                resolved.match.legal_name or resolved.match.canonical_name
            )
        except httpx.TimeoutException as exc:
            return record_mca_source_failure(
                db=db,
                company_id=resolved.match.id,
                company_name=resolved.match.canonical_name,
                query=query,
                error_message="Data.gov.in MCA API timed out. Source is temporarily unavailable.",
            )
        except httpx.HTTPStatusError as exc:
            return record_mca_source_failure(
                db=db,
                company_id=resolved.match.id,
                company_name=resolved.match.canonical_name,
                query=query,
                error_message=f"Data.gov.in MCA API returned HTTP {exc.response.status_code}.",
            )
        except httpx.HTTPError as exc:
            return record_mca_source_failure(
                db=db,
                company_id=resolved.match.id,
                company_name=resolved.match.canonical_name,
                query=query,
                error_message=f"Data.gov.in MCA API request failed: {exc.__class__.__name__}.",
            )

    source = get_or_create_mca_source(db)
    run = CollectionRun(
        company_id=resolved.match.id,
        status="running",
        extra_metadata={"source": MCA_SOURCE_NAME, "query": query},
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
            "company": {"id": resolved.match.id, "canonical_name": resolved.match.canonical_name},
            "collection_run_id": run.id,
            "source": MCA_SOURCE_NAME,
            "record_found": False,
            "mca_record": None,
        }

    normalized = normalize_mca_record(raw_record)
    incorporation_date = parse_mca_date(normalized.get("incorporation_date"))

    resolved.match.cin = normalized.get("cin") or resolved.match.cin
    resolved.match.legal_name = normalized.get("company_name") or resolved.match.legal_name
    resolved.match.incorporation_date = incorporation_date or resolved.match.incorporation_date
    resolved.match.company_status = normalized.get("company_status") or resolved.match.company_status
    resolved.match.company_category = normalized.get("company_category") or resolved.match.company_category

    db.add(
        SourceRecord(
            company_id=resolved.match.id,
            source_id=source.id,
            collection_run_id=run.id,
            record_type="mca_company_master",
            external_id=normalized.get("cin"),
            title=normalized.get("company_name"),
            observed_at=datetime.now(UTC),
            published_at=None,
            confidence="high",
            raw_data=raw_record,
            normalized_data=normalized,
        )
    )

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.extra_metadata = {**run.extra_metadata, "found": True}
    db.commit()

    return {
        "status": "completed",
        "company": {"id": resolved.match.id, "canonical_name": resolved.match.canonical_name},
        "collection_run_id": run.id,
        "source": MCA_SOURCE_NAME,
        "record_found": True,
        "mca_record": normalized,
    }


def record_mca_source_failure(
    db: Session,
    company_id: int,
    company_name: str,
    query: str,
    error_message: str,
) -> dict:
    source = get_or_create_mca_source(db)
    run = CollectionRun(
        company_id=company_id,
        status="failed",
        completed_at=datetime.now(UTC),
        error_message=error_message,
        extra_metadata={
            "source": MCA_SOURCE_NAME,
            "query": query,
            "found": False,
            "source_status": "unavailable",
        },
    )
    db.add(run)
    db.commit()

    return {
        "status": "source_failed",
        "company": {"id": company_id, "canonical_name": company_name},
        "collection_run_id": run.id,
        "source": MCA_SOURCE_NAME,
        "record_found": False,
        "mca_record": None,
        "message": error_message,
    }
