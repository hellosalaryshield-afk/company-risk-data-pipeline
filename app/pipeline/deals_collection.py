from datetime import UTC, datetime, time
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.sources.deal_signals import UNITS, deal_summary, extract_deal_kpis
from app.sources.nse_deals import Deal, NseDealsClient, group_by_symbol

DEALS_SOURCE_NAME = "nse_bulk_block_deals"

MISSING_DEALS = object()


class DealsCollectionError(RuntimeError):
    pass


def get_or_create_deals_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == DEALS_SOURCE_NAME)).first()
    if source:
        return source

    source = DataSource(
        name=DEALS_SOURCE_NAME,
        source_type="file",
        base_url="https://nsearchives.nseindia.com",
        description=(
            "NSE bulk and block deal disclosures, published as daily CSV files. Supplies "
            "large-lot buying and selling by institutions and promoters."
        ),
        requires_auth=False,
        known_limitations=(
            "Each file is a single trading day, so history exists only if this is collected "
            "every trading day. NSE-listed companies only, matched by ticker, so BSE-only and "
            "unlisted companies are never covered. NSE refuses unfamiliar clients, so a browser "
            "user agent is required. An empty day is published as a NO RECORDS row."
        ),
    )
    db.add(source)
    db.flush()
    return source


def nse_symbol(company: Company) -> str | None:
    """Ticker to match against the deals file.

    Only NSE tickers are usable: the file is an NSE publication, and a BSE or foreign ticker
    would either miss or, worse, collide with an unrelated NSE symbol.
    """
    if not company.ticker or (company.exchange or "").upper() != "NSE":
        return None
    return company.ticker.strip().upper()


def collect_deals_for_registry(
    db: Session,
    deals: list[Deal] | object = MISSING_DEALS,
) -> dict:
    """Fetch one day of NSE deals and attach them to every matching company.

    The file is market-wide, so it is fetched once and distributed, rather than called once
    per company.
    """
    if deals is MISSING_DEALS:
        try:
            deals = NseDealsClient().fetch_all_deals()
        except httpx.HTTPError as exc:
            return record_deals_failure(db, f"NSE deals request failed: {exc.__class__.__name__}.")

    source = get_or_create_deals_source(db)
    run = CollectionRun(
        status="running",
        extra_metadata={"source": DEALS_SOURCE_NAME, "deals_in_file": len(deals)},
    )
    db.add(run)
    db.flush()

    by_symbol = group_by_symbol(deals)
    companies = CompanyRepository(db).list_companies()

    matched: list[dict] = []
    for company in companies:
        symbol = nse_symbol(company)
        if not symbol or symbol not in by_symbol:
            continue

        company_deals = by_symbol[symbol]
        kpis = extract_deal_kpis(company_deals)
        if not kpis:
            continue

        summary = deal_summary(company_deals)
        # The deal date is the publication date: NSE discloses these after the trading day.
        # Recording it as such is what makes this source usable in a point-in-time backtest.
        observed_at = datetime.combine(company_deals[0].deal_date, time(0, 0), tzinfo=UTC)

        db.add(
            SourceRecord(
                company_id=company.id,
                source_id=source.id,
                collection_run_id=run.id,
                record_type="nse_deals",
                external_id=f"{symbol}:{company_deals[0].deal_date.isoformat()}",
                title=f"{len(company_deals)} large-lot deals in {symbol}",
                observed_at=observed_at,
                published_at=observed_at,
                confidence="high",
                raw_data={"deals": [deal.raw_data for deal in company_deals]},
                normalized_data=summary,
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
                    period_start=observed_at,
                    period_end=observed_at,
                    confidence="high",
                    extra_metadata={"source": DEALS_SOURCE_NAME, "symbol": symbol},
                )
            )

        matched.append(
            {
                "company_id": company.id,
                "canonical_name": company.canonical_name,
                "symbol": symbol,
                "deal_count": len(company_deals),
                "kpis": kpis,
            }
        )

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.extra_metadata = {
        **run.extra_metadata,
        "symbols_in_file": len(by_symbol),
        "companies_matched": len(matched),
    }
    db.commit()

    return {
        "status": "completed",
        "collection_run_id": run.id,
        "source": DEALS_SOURCE_NAME,
        "deals_in_file": len(deals),
        "symbols_in_file": len(by_symbol),
        "companies_matched": len(matched),
        "matches": matched,
    }


def record_deals_failure(db: Session, error_message: str) -> dict:
    source = get_or_create_deals_source(db)
    run = CollectionRun(
        status="failed",
        completed_at=datetime.now(UTC),
        error_message=error_message,
        extra_metadata={"source": DEALS_SOURCE_NAME, "source_status": "unavailable"},
    )
    db.add(run)
    db.commit()

    return {
        "status": "source_failed",
        "collection_run_id": run.id,
        "source": DEALS_SOURCE_NAME,
        "deals_in_file": 0,
        "symbols_in_file": 0,
        "companies_matched": 0,
        "matches": [],
        "message": error_message,
    }
