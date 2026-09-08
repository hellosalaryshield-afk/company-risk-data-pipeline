from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.companies.segments import LISTED_SEGMENTS
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.sources.market_data import (
    MarketQuote,
    UnsupportedExchangeError,
    YahooMarketDataClient,
    build_symbol,
    normalize_market_quote,
)
from app.sources.market_signals import extract_market_kpis, kpi_unit

MARKET_SOURCE_NAME = "yahoo_finance_chart"

MISSING_QUOTE = object()


class MarketCollectionError(RuntimeError):
    pass


def get_or_create_market_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == MARKET_SOURCE_NAME)).first()
    if source:
        return source

    source = DataSource(
        name=MARKET_SOURCE_NAME,
        source_type="api",
        base_url="https://query1.finance.yahoo.com",
        description="Yahoo Finance public chart endpoint for listed-company market risk signals.",
        requires_auth=False,
        known_limitations=(
            "Listed companies only. Needs a correct ticker/exchange pair. Undocumented public "
            "endpoint with no published rate limit; the quoteSummary endpoint requires a crumb "
            "and is not used."
        ),
    )
    db.add(source)
    db.flush()
    return source


def collect_market_for_company(
    db: Session,
    query: str,
    range_period: str = "6mo",
    interval: str = "1d",
    quote: MarketQuote | None | object = MISSING_QUOTE,
) -> dict:
    repository = CompanyRepository(db)
    resolved = CompanyResolver(repository).resolve(query)
    if not resolved.match:
        return {
            "status": resolved.status,
            "query": query,
            "confidence": resolved.confidence,
            "message": "Company could not be resolved. Pick a candidate or add it to the registry first.",
            "candidates": [candidate.canonical_name for candidate in resolved.candidates],
        }

    company = resolved.match
    classification = repository.classify_segment(company)
    segment_label = classification.segment or "undetermined"

    if classification.segment not in LISTED_SEGMENTS:
        db.commit()
        return not_applicable_response(
            company=company,
            segment=classification.segment,
            message=(
                "Yahoo Finance market data covers listed companies only. "
                f"Resolved segment: {segment_label}."
            ),
        )

    try:
        symbol = build_symbol(company.ticker, company.exchange)
    except UnsupportedExchangeError as exc:
        db.commit()
        return not_applicable_response(company=company, segment=classification.segment, message=str(exc))

    if not symbol:
        db.commit()
        return not_applicable_response(
            company=company,
            segment=classification.segment,
            message="Company has no ticker recorded, so no market symbol could be built.",
        )

    if quote is MISSING_QUOTE:
        try:
            quote = YahooMarketDataClient().fetch_price_history(
                symbol,
                range_period=range_period,
                interval=interval,
            )
        except httpx.TimeoutException:
            return record_market_source_failure(
                db=db,
                company_id=company.id,
                company_name=company.canonical_name,
                query=query,
                symbol=symbol,
                error_message="Yahoo Finance chart API timed out. Source is temporarily unavailable.",
            )
        except httpx.HTTPStatusError as exc:
            return record_market_source_failure(
                db=db,
                company_id=company.id,
                company_name=company.canonical_name,
                query=query,
                symbol=symbol,
                error_message=f"Yahoo Finance chart API returned HTTP {exc.response.status_code}.",
            )
        except httpx.HTTPError as exc:
            return record_market_source_failure(
                db=db,
                company_id=company.id,
                company_name=company.canonical_name,
                query=query,
                symbol=symbol,
                error_message=f"Yahoo Finance chart API request failed: {exc.__class__.__name__}.",
            )

    source = get_or_create_market_source(db)
    run = CollectionRun(
        company_id=company.id,
        status="running",
        extra_metadata={
            "source": MARKET_SOURCE_NAME,
            "query": query,
            "symbol": symbol,
            "segment": classification.segment,
            "range": range_period,
            "interval": interval,
        },
    )
    db.add(run)
    db.flush()

    if quote is None:
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.extra_metadata = {**run.extra_metadata, "found": False}
        db.commit()
        return {
            "status": "completed",
            "company": company_payload(company, classification.segment),
            "collection_run_id": run.id,
            "source": MARKET_SOURCE_NAME,
            "symbol": symbol,
            "record_found": False,
            "market_record": None,
            "kpis": {},
            "message": f"Yahoo Finance returned no data for symbol {symbol}. The ticker may be stale or delisted.",
        }

    normalized = normalize_market_quote(quote)
    kpis = extract_market_kpis(quote)

    db.add(
        SourceRecord(
            company_id=company.id,
            source_id=source.id,
            collection_run_id=run.id,
            record_type="market_price_history",
            external_id=quote.symbol,
            title=quote.long_name,
            observed_at=quote.period_end or datetime.now(UTC),
            published_at=quote.period_end,
            confidence="high",
            raw_data=quote.raw_data,
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
                unit=kpi_unit(kpi_name, quote.currency),
                period_start=quote.period_start,
                period_end=quote.period_end,
                observed_at=quote.period_end or datetime.now(UTC),
                published_at=quote.period_end,
                confidence="high",
                extra_metadata={
                    "source": MARKET_SOURCE_NAME,
                    "symbol": quote.symbol,
                    "exchange": quote.exchange_name,
                    "range": range_period,
                    "interval": interval,
                    "segment": classification.segment,
                },
            )
        )

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.extra_metadata = {**run.extra_metadata, "found": True}
    db.commit()

    return {
        "status": "completed",
        "company": company_payload(company, classification.segment),
        "collection_run_id": run.id,
        "source": MARKET_SOURCE_NAME,
        "symbol": quote.symbol,
        "record_found": True,
        "market_record": normalized,
        "kpis": kpis,
    }


def company_payload(company: Company, segment: str | None) -> dict:
    return {
        "id": company.id,
        "canonical_name": company.canonical_name,
        "company_segment": segment,
    }


def not_applicable_response(company: Company, segment: str | None, message: str) -> dict:
    return {
        "status": "not_applicable",
        "company": company_payload(company, segment),
        "source": MARKET_SOURCE_NAME,
        "record_found": False,
        "market_record": None,
        "kpis": {},
        "message": message,
    }


def record_market_source_failure(
    db: Session,
    company_id: int,
    company_name: str,
    query: str,
    symbol: str,
    error_message: str,
) -> dict:
    source = get_or_create_market_source(db)
    run = CollectionRun(
        company_id=company_id,
        status="failed",
        completed_at=datetime.now(UTC),
        error_message=error_message,
        extra_metadata={
            "source": MARKET_SOURCE_NAME,
            "query": query,
            "symbol": symbol,
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
        "source": MARKET_SOURCE_NAME,
        "symbol": symbol,
        "record_found": False,
        "market_record": None,
        "kpis": {},
        "message": error_message,
    }
