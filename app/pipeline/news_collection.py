from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.settings import Settings
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.source import DataSource
from app.sources.base import SourceFetchResult
from app.sources.news_signals import extract_news_kpis
from app.sources.newsapi import NewsApiClient

NEWS_SOURCE_NAME = "newsapi"


class NewsCollectionError(RuntimeError):
    pass


def get_or_create_news_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == NEWS_SOURCE_NAME)).first()
    if source:
        return source

    source = DataSource(
        name=NEWS_SOURCE_NAME,
        source_type="api",
        base_url="https://newsapi.org",
        description="NewsAPI everything endpoint for company-level news signals.",
        requires_auth=True,
        known_limitations="Coverage, source availability, and historical lookback depend on NewsAPI plan.",
    )
    db.add(source)
    db.flush()
    return source


def collect_news_for_company(
    db: Session,
    query: str,
    settings: Settings,
    days_back: int = 30,
    page_size: int = 25,
    fetch_result: SourceFetchResult | None = None,
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

    if fetch_result is None:
        if not settings.news_api_key:
            raise NewsCollectionError("NEWS_API_KEY is not configured.")
        fetch_result = NewsApiClient(settings.news_api_key).fetch_company_news(
            resolved.match.canonical_name,
            days_back=days_back,
            page_size=page_size,
        )

    source = get_or_create_news_source(db)
    run = CollectionRun(
        company_id=resolved.match.id,
        status="running",
        extra_metadata={
            "source": NEWS_SOURCE_NAME,
            "query": query,
            "days_back": days_back,
            "page_size": page_size,
            "fetch_metadata": fetch_result.metadata,
        },
    )
    db.add(run)
    db.flush()

    article_texts: list[str] = []
    source_record_count = 0

    for article in fetch_result.records:
        article_texts.append(f"{article.title or ''} {article.description or ''}".strip())
        db.add(
            SourceRecord(
                company_id=resolved.match.id,
                source_id=source.id,
                collection_run_id=run.id,
                record_type="news_article",
                external_id=article.url,
                title=article.title[:500] if article.title else None,
                url=article.url,
                observed_at=article.published_at,
                published_at=article.published_at,
                confidence="medium",
                raw_data=article.raw_data,
                normalized_data={
                    "title": article.title,
                    "description": article.description,
                    "url": article.url,
                    "source_name": article.source_name,
                    "published_at": article.published_at.isoformat() if article.published_at else None,
                },
            )
        )
        source_record_count += 1

    kpis = extract_news_kpis(article_texts)
    for kpi_name, value in kpis.items():
        db.add(
            KpiObservation(
                company_id=resolved.match.id,
                source_id=source.id,
                kpi_name=kpi_name,
                value_numeric=Decimal(value),
                unit="count",
                observed_at=datetime.now(UTC),
                published_at=datetime.now(UTC),
                confidence="medium",
                extra_metadata={
                    "source": NEWS_SOURCE_NAME,
                    "query": resolved.match.canonical_name,
                    "days_back": days_back,
                },
            )
        )

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    db.commit()

    return {
        "status": "completed",
        "company": {
            "id": resolved.match.id,
            "canonical_name": resolved.match.canonical_name,
        },
        "collection_run_id": run.id,
        "source": NEWS_SOURCE_NAME,
        "records_stored": source_record_count,
        "kpis": kpis,
    }
