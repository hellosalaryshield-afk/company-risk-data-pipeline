"""Collect a listed company's financial results (revenue, margin, cash runway, debt)."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.companies.segments import LISTED_SEGMENTS
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.sources.financials import (
    BALANCE_SHEET_KPIS,
    KPI_UNITS,
    Financials,
    YahooFundamentalsClient,
    financial_kpis,
    is_financial_institution,
)
from app.sources.market_data import UnsupportedExchangeError, build_symbol

FINANCIALS_SOURCE_NAME = "yahoo_finance_fundamentals"
MISSING = object()


def get_or_create_financials_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == FINANCIALS_SOURCE_NAME)).first()
    if source:
        return source
    source = DataSource(
        name=FINANCIALS_SOURCE_NAME,
        source_type="api",
        base_url="https://query2.finance.yahoo.com",
        description="Yahoo Finance fundamentals timeseries: quarterly, annual and trailing financial results.",
        requires_auth=False,
        known_limitations=(
            "Listed companies only (NSE and US exchanges tested). Undocumented public endpoint with "
            "no published rate limit. Indian companies report some balance-sheet items only every "
            "six months, so those fall back to the last annual report."
        ),
    )
    db.add(source)
    db.flush()
    return source


def collect_financials_for_company(db: Session, query: str, financials: Financials | object = MISSING) -> dict:
    repository = CompanyRepository(db)
    resolved = CompanyResolver(repository).resolve(query)
    if not resolved.match:
        return {
            "status": resolved.status,
            "query": query,
            "message": "Company could not be resolved. Pick a candidate or add it to the registry first.",
            "candidates": [candidate.canonical_name for candidate in resolved.candidates],
        }

    company = resolved.match
    segment = repository.classify_segment(company).segment
    if segment not in LISTED_SEGMENTS:
        db.commit()
        return not_applicable(company, "Financial results are collected for listed companies only.")
    try:
        symbol = build_symbol(company.ticker, company.exchange)
    except UnsupportedExchangeError as exc:
        db.commit()
        return not_applicable(company, str(exc))
    if not symbol:
        db.commit()
        return not_applicable(company, "Company has no ticker recorded.")

    if financials is MISSING:
        try:
            financials = YahooFundamentalsClient().fetch(symbol)
        except (httpx.HTTPError, ValueError) as exc:
            return record_failure(db, company, symbol, f"Yahoo Finance fundamentals request failed: {exc.__class__.__name__}.")

    source = get_or_create_financials_source(db)
    now = datetime.now(UTC)
    run = CollectionRun(
        company_id=company.id,
        status="running",
        extra_metadata={"source": FINANCIALS_SOURCE_NAME, "query": query, "symbol": symbol, "segment": segment},
    )
    db.add(run)
    db.flush()

    financial_institution = is_financial_institution(company.sector, company.industry)
    values, details = financial_kpis(financials, financial_institution=financial_institution)
    if financial_institution:
        # Older collections may have stored these before the rule existed; drop them so
        # the score never reads a cash runway for a bank.
        db.execute(delete(KpiObservation).where(
            KpiObservation.company_id == company.id, KpiObservation.kpi_name.in_(BALANCE_SHEET_KPIS)
        ))
    run.status = "completed"
    run.completed_at = now
    run.extra_metadata = {**run.extra_metadata, "found": bool(values)}
    if not values:
        db.commit()
        return {
            "status": "completed",
            "company": {"id": company.id, "canonical_name": company.canonical_name},
            "collection_run_id": run.id,
            "source": FINANCIALS_SOURCE_NAME,
            "symbol": symbol,
            "record_found": False,
            "kpis": {},
            "message": f"Yahoo Finance has no financial results for {symbol}.",
        }

    period_end = financials.latest_period_end
    period_end_dt = datetime(period_end.year, period_end.month, period_end.day, tzinfo=UTC) if period_end else None
    record = SourceRecord(
        company_id=company.id,
        source_id=source.id,
        collection_run_id=run.id,
        record_type="financial_results",
        external_id=symbol,
        title=f"{company.canonical_name} financial results",
        # Results become known when we collect them, not on the report date, so the score
        # never "sees" figures before they were public.
        observed_at=now,
        published_at=period_end_dt,
        confidence="high",
        raw_data=financials.raw_data,
        normalized_data={"symbol": symbol, "currency": financials.currency, "kpis": values, "details": details},
    )
    db.add(record)
    db.flush()

    for kpi_name, value in values.items():
        detail = details.get(kpi_name, {})
        db.add(
            KpiObservation(
                company_id=company.id,
                source_id=source.id,
                source_record_id=record.id,
                kpi_name=kpi_name,
                value_numeric=Decimal(str(value)),
                unit=KPI_UNITS[kpi_name],
                period_end=period_end_dt,
                observed_at=now,
                published_at=period_end_dt,
                confidence="high",
                extra_metadata={"source": FINANCIALS_SOURCE_NAME, "symbol": symbol,
                                "currency": financials.currency, **detail},
            )
        )
    db.commit()
    return {
        "status": "completed",
        "company": {"id": company.id, "canonical_name": company.canonical_name},
        "collection_run_id": run.id,
        "source": FINANCIALS_SOURCE_NAME,
        "symbol": symbol,
        "record_found": True,
        "kpis": values,
    }


def not_applicable(company: Company, message: str) -> dict:
    return {
        "status": "not_applicable",
        "company": {"id": company.id, "canonical_name": company.canonical_name},
        "source": FINANCIALS_SOURCE_NAME,
        "record_found": False,
        "kpis": {},
        "message": message,
    }


def record_failure(db: Session, company: Company, symbol: str, message: str) -> dict:
    get_or_create_financials_source(db)
    run = CollectionRun(
        company_id=company.id,
        status="failed",
        completed_at=datetime.now(UTC),
        error_message=message,
        extra_metadata={"source": FINANCIALS_SOURCE_NAME, "symbol": symbol, "found": False},
    )
    db.add(run)
    db.commit()
    return {
        "status": "source_failed",
        "company": {"id": company.id, "canonical_name": company.canonical_name},
        "collection_run_id": run.id,
        "source": FINANCIALS_SOURCE_NAME,
        "symbol": symbol,
        "record_found": False,
        "kpis": {},
        "message": message,
    }
