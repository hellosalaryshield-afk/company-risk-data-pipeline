from datetime import UTC, datetime

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.pipeline.macro_collection import MACRO_RECORD_TYPE, latest_macro_snapshot
from app.pipeline.source_selection import sources_for_segment

# Human-readable labels so the report never shows a bare column name.
KPI_LABELS = {
    "news_article_count": "News articles found",
    "layoff_news_count": "Articles mentioning layoffs",
    "restructuring_news_count": "Articles mentioning restructuring",
    "funding_news_count": "Articles mentioning funding",
    "distress_news_count": "Articles mentioning distress",
    "market_price": "Share price",
    "market_price_change_pct_period": "Price change over period",
    "market_drawdown_from_52w_high_pct": "Below 52-week high",
    "market_premium_over_52w_low_pct": "Above 52-week low",
    "market_max_drawdown_pct_period": "Worst drawdown in period",
    "market_annualized_volatility_pct": "Annualised volatility",
    "market_trading_volume": "Trading volume",
}

# KPIs where a higher value means more layoff risk, used only to word the plain-language
# notes. This is descriptive wording, not a model.
HIGHER_IS_RISKIER = {
    "layoff_news_count",
    "restructuring_news_count",
    "distress_news_count",
    "market_drawdown_from_52w_high_pct",
    "market_max_drawdown_pct_period",
    "market_annualized_volatility_pct",
}

COVERAGE_BANDS = (
    (0.75, "Good", "Most applicable sources returned data for this company."),
    (0.40, "Partial", "Some applicable sources returned data; treat trends as indicative only."),
    (0.0, "Thin", "Very little company-specific data is available. Sector-level context should be preferred."),
)


def resolve_for_report(db: Session, query: str) -> dict:
    """Resolve a typed name, returning candidates instead of guessing when ambiguous."""
    repository = CompanyRepository(db)
    resolved = CompanyResolver(repository).resolve(query)
    if resolved.match:
        return {"status": "matched", "company": resolved.match, "candidates": []}

    return {
        "status": resolved.status,
        "company": None,
        "candidates": [
            {"id": candidate.id, "canonical_name": candidate.canonical_name}
            for candidate in resolved.candidates
        ],
    }


def latest_kpis(db: Session, company_id: int) -> list[dict]:
    """Latest value per KPI, with the previous value so a direction can be shown."""
    rows = db.execute(
        select(KpiObservation.kpi_name, KpiObservation.value_numeric, KpiObservation.unit, KpiObservation.observed_at)
        .where(KpiObservation.company_id == company_id)
        .order_by(KpiObservation.kpi_name, desc(KpiObservation.observed_at), desc(KpiObservation.id))
    ).all()

    grouped: dict[str, list] = {}
    for name, value, unit, observed_at in rows:
        grouped.setdefault(name, []).append((value, unit, observed_at))

    results: list[dict] = []
    for name, entries in grouped.items():
        current_value, unit, observed_at = entries[0]
        previous_value = entries[1][0] if len(entries) > 1 else None
        current = float(current_value) if current_value is not None else None
        previous = float(previous_value) if previous_value is not None else None

        results.append(
            {
                "kpi_name": name,
                "label": KPI_LABELS.get(name, name.replace("_", " ").capitalize()),
                "value": current,
                "previous_value": previous,
                "direction": direction_for(current, previous),
                "unit": unit,
                "observed_at": observed_at,
                "observation_count": len(entries),
                "higher_is_riskier": name in HIGHER_IS_RISKIER,
            }
        )

    results.sort(key=lambda item: item["label"])
    return results


def direction_for(current: float | None, previous: float | None) -> str:
    if current is None or previous is None:
        return "no_history"
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


def source_health(db: Session, company_id: int) -> list[dict]:
    """Most recent collection run per source for this company."""
    sources = db.scalars(select(DataSource).order_by(DataSource.name)).all()
    health: list[dict] = []

    for source in sources:
        run = db.scalars(
            select(CollectionRun)
            .where(CollectionRun.company_id == company_id)
            .where(CollectionRun.extra_metadata["source"].as_string() == source.name)
            .order_by(desc(CollectionRun.started_at))
            .limit(1)
        ).first()

        record_count = db.scalar(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.company_id == company_id)
            .where(SourceRecord.source_id == source.id)
        )

        health.append(
            {
                "name": source.name,
                "requires_auth": source.requires_auth,
                "status": run.status if run else "never_run",
                "last_run_at": run.started_at if run else None,
                "error_message": run.error_message if run else None,
                "records_stored": record_count or 0,
                "known_limitations": source.known_limitations,
            }
        )

    return health


def coverage_band(applicable: int, succeeded: int) -> dict:
    ratio = (succeeded / applicable) if applicable else 0.0
    for threshold, label, note in COVERAGE_BANDS:
        if ratio >= threshold:
            return {"ratio": round(ratio, 2), "band": label, "note": note}
    return {"ratio": 0.0, "band": "Thin", "note": COVERAGE_BANDS[-1][2]}


def plain_language_notes(company: Company, segment: str | None, kpis: list[dict], coverage: dict) -> list[str]:
    """Descriptive sentences about what was collected. Not a prediction."""
    notes: list[str] = []
    by_name = {kpi["kpi_name"]: kpi for kpi in kpis}

    drawdown = by_name.get("market_drawdown_from_52w_high_pct")
    if drawdown and drawdown["value"] is not None:
        if drawdown["value"] >= 25:
            notes.append(
                f"The share price is {drawdown['value']:.1f}% below its 52-week high, "
                "which is a large decline over the last year."
            )
        else:
            notes.append(f"The share price is {drawdown['value']:.1f}% below its 52-week high.")

    volatility = by_name.get("market_annualized_volatility_pct")
    if volatility and volatility["value"] is not None:
        notes.append(f"Annualised share price volatility over the collected window is {volatility['value']:.1f}%.")

    layoffs = by_name.get("layoff_news_count")
    articles = by_name.get("news_article_count")
    if layoffs and articles and articles["value"]:
        if layoffs["value"]:
            notes.append(
                f"{int(layoffs['value'])} of {int(articles['value'])} recent articles mention layoffs or job cuts."
            )
        else:
            notes.append(f"None of the {int(articles['value'])} recent articles mention layoffs or job cuts.")

    if segment is None:
        notes.append(
            "This company's segment could not be determined because its funding status is not recorded, "
            "so segment-specific sources were not run."
        )

    notes.append(coverage["note"])
    return notes


def build_company_report(db: Session, company: Company, range_label: str = "last collection window") -> dict:
    repository = CompanyRepository(db)
    classification = repository.classify_segment(company)
    db.commit()

    kpis = latest_kpis(db, company.id)
    health = source_health(db, company.id)
    applicable = sources_for_segment(classification.segment)
    succeeded = [
        item for item in health if item["name"] in applicable and item["status"] == "completed" and item["records_stored"]
    ]
    coverage = coverage_band(len(applicable), len(succeeded))

    macro_record_count = db.scalar(
        select(func.count()).select_from(SourceRecord).where(SourceRecord.record_type == MACRO_RECORD_TYPE)
    )

    return {
        "generated_at": datetime.now(UTC),
        "range_label": range_label,
        "company": {
            "id": company.id,
            "canonical_name": company.canonical_name,
            "legal_name": company.legal_name,
            "country": company.country,
            "sector": company.sector,
            "industry": company.industry,
            "cin": company.cin,
            "ticker": company.ticker,
            "exchange": company.exchange,
            "website": company.website,
        },
        "segment": {
            "segment": classification.segment,
            "geography": classification.geography,
            "listing_status": classification.listing_status,
            "funding_status": classification.funding_status,
            "confidence": classification.confidence,
            "reasons": classification.reasons,
        },
        "applicable_sources": list(applicable),
        "kpis": kpis,
        "source_health": health,
        "coverage": coverage,
        "macro": latest_macro_snapshot(db) if macro_record_count else [],
        "notes": plain_language_notes(company, classification.segment, kpis, coverage),
    }
