from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.collection import CollectionRun, SourceRecord
from app.pipeline.market_collection import get_or_create_market_source
from app.sources.market_data import YahooMarketDataClient, normalize_market_quote
from app.sources.market_signals import period_change_pct

MACRO_RECORD_TYPE = "macro_indicator"


@dataclass(frozen=True)
class MacroIndicator:
    key: str
    symbol: str
    label: str
    category: str


# Phase 3 asks for industry-level and macro covariates. These all come from the same
# keyless Yahoo chart endpoint already used for company market data, so they add no new
# credential, cost, or vendor.
MACRO_INDICATORS: tuple[MacroIndicator, ...] = (
    MacroIndicator("usd_inr", "INR=X", "USD/INR", "macro"),
    MacroIndicator("gold_usd", "GC=F", "Gold (COMEX front month)", "macro"),
    MacroIndicator("us_13w_tbill", "^IRX", "US 13-week T-bill rate", "macro"),
    MacroIndicator("nifty_50", "^NSEI", "NIFTY 50", "market_index"),
    MacroIndicator("nifty_it", "^CNXIT", "NIFTY IT", "industry_index"),
    MacroIndicator("sp_500", "^GSPC", "S&P 500", "market_index"),
)

INDICATORS_BY_KEY = {indicator.key: indicator for indicator in MACRO_INDICATORS}


def collect_macro_indicators(
    db: Session,
    range_period: str = "6mo",
    interval: str = "1d",
    keys: list[str] | None = None,
) -> dict:
    """Collect macro and index series and store them as company-independent records.

    These rows carry `company_id = NULL` because they describe the environment, not one
    company. Feature engineering joins them on observation date later.
    """
    selected = [INDICATORS_BY_KEY[key] for key in keys if key in INDICATORS_BY_KEY] if keys else list(MACRO_INDICATORS)
    source = get_or_create_market_source(db)
    run = CollectionRun(
        status="running",
        extra_metadata={
            "source": source.name,
            "record_type": MACRO_RECORD_TYPE,
            "range": range_period,
            "interval": interval,
            "indicators": [indicator.key for indicator in selected],
        },
    )
    db.add(run)
    db.flush()

    client = YahooMarketDataClient()
    collected: list[dict] = []
    failures: list[dict] = []

    for indicator in selected:
        try:
            quote = client.fetch_price_history(indicator.symbol, range_period=range_period, interval=interval)
        except httpx.HTTPError as exc:
            failures.append({"key": indicator.key, "message": f"{exc.__class__.__name__}"})
            continue

        if quote is None:
            failures.append({"key": indicator.key, "message": "No data returned."})
            continue

        normalized = {
            **normalize_market_quote(quote),
            "indicator_key": indicator.key,
            "indicator_label": indicator.label,
            "category": indicator.category,
            "change_pct_period": period_change_pct(quote.closes),
        }
        db.add(
            SourceRecord(
                company_id=None,
                source_id=source.id,
                collection_run_id=run.id,
                record_type=MACRO_RECORD_TYPE,
                external_id=indicator.key,
                title=indicator.label,
                observed_at=quote.period_end or datetime.now(UTC),
                published_at=quote.period_end,
                confidence="high",
                raw_data=quote.raw_data,
                normalized_data=normalized,
            )
        )
        collected.append(
            {
                "key": indicator.key,
                "label": indicator.label,
                "category": indicator.category,
                "value": quote.price,
                "change_pct_period": normalized["change_pct_period"],
            }
        )

    run.status = "completed" if collected else "failed"
    run.completed_at = datetime.now(UTC)
    if failures and not collected:
        run.error_message = "; ".join(f"{failure['key']}: {failure['message']}" for failure in failures)
    run.extra_metadata = {**run.extra_metadata, "collected": len(collected), "failed": len(failures)}
    db.commit()

    return {
        "status": run.status,
        "collection_run_id": run.id,
        "range": range_period,
        "indicators_collected": collected,
        "indicators_failed": failures,
    }


def latest_macro_snapshot(db: Session) -> list[dict]:
    """Return the most recent stored value for each macro indicator."""
    snapshot: list[dict] = []
    for indicator in MACRO_INDICATORS:
        record = db.scalars(
            select(SourceRecord)
            .where(SourceRecord.record_type == MACRO_RECORD_TYPE)
            .where(SourceRecord.external_id == indicator.key)
            .order_by(desc(SourceRecord.collected_at))
            .limit(1)
        ).first()
        if not record:
            continue

        normalized = record.normalized_data or {}
        snapshot.append(
            {
                "key": indicator.key,
                "label": indicator.label,
                "category": indicator.category,
                "value": normalized.get("price"),
                "currency": normalized.get("currency"),
                "change_pct_period": normalized.get("change_pct_period"),
                "observed_at": record.observed_at,
            }
        )
    return snapshot
