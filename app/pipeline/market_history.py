from datetime import UTC, datetime
from decimal import Decimal
from statistics import median

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.models.collection import CollectionRun, KpiObservation
from app.models.company import Company
from app.pipeline.market_collection import MARKET_SOURCE_NAME, get_or_create_market_source
from app.sources.market_data import (
    REQUEST_HEADERS,
    YAHOO_CHART_ENDPOINT,
    UnsupportedExchangeError,
    build_symbol,
    parse_dated_closes,
)
from app.sources.market_signals import (
    annualized_volatility_pct,
    max_drawdown_pct,
    period_change_pct,
)

# The model has to be trained and backtested on history, and until now the pipeline stored a
# single current value per KPI: five distinct dates across the whole database. Yahoo already
# returns the full series on the same free call, so the history was being fetched and thrown
# away. This module keeps it.
#
# Every historical value is computed from a TRAILING window ending at that date. Using the
# whole series to compute a 2022 figure would put 2026 information inside it, which is exactly
# the leakage the Phase 3 check exists to catch.

PERIODS_PER_YEAR = {"1d": 252, "1wk": 52, "1mo": 12}

# One year of trailing context at each interval, so "52-week high" means the same thing
# regardless of sampling.
LOOKBACK_PERIODS = {"1d": 252, "1wk": 52, "1mo": 12}

def infer_interval(points: list[tuple[datetime, float]]) -> str:
    """Work out the real sampling interval from the data itself.

    Yahoo does not honour the requested interval uniformly: asking for `max`/`1mo` returns
    monthly bars for a company listed in 2002 but daily bars for one listed in 2024, because
    the short history fits inside its point budget. Trusting the requested value silently
    computed monthly volatility on daily bars and understated it by sqrt(252/12), roughly
    4.6x. The spacing between points is the only reliable signal, so it is measured.
    """
    if len(points) < 3:
        return "1mo"

    gaps = [
        (points[i + 1][0] - points[i][0]).days
        for i in range(len(points) - 1)
        if (points[i + 1][0] - points[i][0]).days > 0
    ]
    if not gaps:
        return "1mo"

    typical = median(gaps)
    if typical <= 4:
        return "1d"
    if typical <= 10:
        return "1wk"
    return "1mo"


HISTORY_KPIS = (
    "market_price",
    "market_price_change_pct_period",
    "market_drawdown_from_52w_high_pct",
    "market_premium_over_52w_low_pct",
    "market_max_drawdown_pct_period",
    "market_annualized_volatility_pct",
)


class MarketHistoryError(RuntimeError):
    pass


def fetch_dated_history(symbol: str, range_period: str, interval: str, timeout: float = 30.0):
    response = httpx.get(
        YAHOO_CHART_ENDPOINT.format(symbol=symbol),
        params={"range": range_period, "interval": interval},
        headers=REQUEST_HEADERS,
        timeout=timeout,
    )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return parse_dated_closes(response.json())


def kpis_at_index(
    points: list[tuple[datetime, float]],
    index: int,
    lookback: int,
    periods_per_year: int,
) -> dict[str, float]:
    """KPIs as they would have been computable on the date at `index`.

    Only points at or before `index` are used, and only the trailing `lookback` of them, so
    the result contains nothing that had not happened yet.
    """
    window = points[max(0, index - lookback + 1) : index + 1]
    if len(window) < 2:
        return {}

    closes = [close for _, close in window]
    price = closes[-1]
    high = max(closes)
    low = min(closes)

    kpis: dict[str, float] = {"market_price": round(price, 4)}

    change = period_change_pct(closes)
    if change is not None:
        kpis["market_price_change_pct_period"] = round(change, 4)

    if high > 0:
        kpis["market_drawdown_from_52w_high_pct"] = round((high - price) / high * 100, 4)
    if low > 0:
        kpis["market_premium_over_52w_low_pct"] = round((price - low) / low * 100, 4)

    drawdown = max_drawdown_pct(closes)
    if drawdown is not None:
        kpis["market_max_drawdown_pct_period"] = round(drawdown, 4)

    volatility = annualized_volatility_pct(closes, periods_per_year=periods_per_year)
    if volatility is not None:
        kpis["market_annualized_volatility_pct"] = round(volatility, 4)

    return kpis


def existing_history_dates(db: Session, company_id: int) -> set:
    """Dates already stored, so a re-run tops up rather than duplicating."""
    rows = db.execute(
        select(KpiObservation.published_at)
        .where(KpiObservation.company_id == company_id)
        .where(KpiObservation.kpi_name == "market_price")
    ).all()
    return {row[0].date() for row in rows if row[0] is not None}


def backfill_company(
    db: Session,
    company: Company,
    range_period: str = "max",
    interval: str = "1mo",
    points: list[tuple[datetime, float]] | None = None,
) -> dict:
    try:
        symbol = build_symbol(company.ticker, company.exchange)
    except UnsupportedExchangeError as exc:
        return {
            "status": "not_applicable",
            "company": company.canonical_name,
            "observations_written": 0,
            "message": str(exc),
        }
    if not symbol:
        return {"status": "not_applicable", "company": company.canonical_name, "observations_written": 0}

    if points is None:
        try:
            points = fetch_dated_history(symbol, range_period, interval)
        except httpx.HTTPError as exc:
            return {
                "status": "source_failed",
                "company": company.canonical_name,
                "observations_written": 0,
                "message": f"{exc.__class__.__name__}",
            }

    if not points:
        return {"status": "completed", "company": company.canonical_name, "observations_written": 0}

    source = get_or_create_market_source(db)
    # The interval actually returned, not the one requested. See infer_interval.
    actual_interval = infer_interval(points)
    lookback = LOOKBACK_PERIODS.get(actual_interval, 12)
    periods_per_year = PERIODS_PER_YEAR.get(actual_interval, 12)
    already = existing_history_dates(db, company.id)

    written = 0
    skipped = 0

    for index, (point_date, _close) in enumerate(points):
        if point_date.date() in already:
            skipped += 1
            continue

        kpis = kpis_at_index(points, index, lookback, periods_per_year)
        if not kpis:
            continue

        window_start = points[max(0, index - lookback + 1)][0]

        for kpi_name, value in kpis.items():
            db.add(
                KpiObservation(
                    company_id=company.id,
                    source_id=source.id,
                    kpi_name=kpi_name,
                    value_numeric=Decimal(str(value)),
                    unit="percent" if kpi_name.endswith("_pct") else "price",
                    observed_at=point_date,
                    published_at=point_date,
                    period_start=window_start,
                    period_end=point_date,
                    confidence="high",
                    extra_metadata={
                        "source": MARKET_SOURCE_NAME,
                        "symbol": symbol,
                        "interval": actual_interval,
                        "requested_interval": interval,
                        "backfill": True,
                    },
                )
            )
        written += 1

    db.commit()

    return {
        "status": "completed",
        "company": company.canonical_name,
        "symbol": symbol,
        "interval": actual_interval,
        "points_available": len(points),
        "observations_written": written,
        "observations_skipped": skipped,
        "earliest": points[0][0].date().isoformat(),
        "latest": points[-1][0].date().isoformat(),
    }


def backfill_registry(
    db: Session,
    range_period: str = "max",
    interval: str = "1mo",
    limit: int | None = None,
) -> dict:
    """Backfill every listed company in the registry."""
    companies = [c for c in CompanyRepository(db).list_companies() if c.ticker and c.exchange]
    if limit:
        companies = companies[:limit]

    run = CollectionRun(
        status="running",
        extra_metadata={
            "source": MARKET_SOURCE_NAME,
            "job": "market_history_backfill",
            "range": range_period,
            "interval": interval,
        },
    )
    db.add(run)
    db.flush()

    results = []
    total_written = 0
    for company in companies:
        result = backfill_company(db, company, range_period=range_period, interval=interval)
        results.append(result)
        total_written += result.get("observations_written", 0)

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.extra_metadata = {
        **run.extra_metadata,
        "companies": len(companies),
        "observations_written": total_written,
    }
    db.commit()

    return {
        "status": "completed",
        "collection_run_id": run.id,
        "companies": len(companies),
        "observations_written": total_written,
        "results": results,
    }
