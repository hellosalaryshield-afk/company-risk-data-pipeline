"""Step 1: gather one company's inputs from the database, as known on a given date.

Missing data stays None, never 0 (Developer Instructions v1, section 10).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.features.point_in_time import ensure_utc
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.pipeline.news_signal_collection import CLASSIFIED_RECORD_TYPE, NEWS_SIGNAL_SOURCE_NAME
from app.scoring.engine import NewsEvent

MCA_SOURCE_NAME = "data_gov_mca_company_master"

# Feature key -> KPI the pipeline already stores.
KPI_FEATURES = {
    "revenue_growth_yoy": "fin_revenue_growth_yoy_pct",
    "operating_margin": "fin_operating_margin_pct",
    "cash_runway_months": "fin_cash_runway_months",
    "debt_to_equity": "fin_debt_to_equity",
    "share_price_change_6m": "market_price_change_pct_period",
    "glassdoor_rating": "glassdoor_rating",
    "positive_outlook_share": "glassdoor_business_outlook_rating",
    "ceo_approval_share": "glassdoor_ceo_rating",
    "recommend_share": "glassdoor_recommend_to_friend_rating",
    "senior_management_rating": "glassdoor_senior_management_rating",
}

# MCA statuses that mean the company has stopped filing. The registry carries no filing
# dates, so the status stands in for "filings overdue". It catches only serious cases: a
# company a year late but still Active reads as up to date.
FILINGS_OVERDUE_STATUSES = {
    "inactive for e-filing",
    "inactive",
    "under process of striking off",
    "under process of strike-off",
    "strike off-awaitingpublication",
    "not available for efiling",
    "not available for e-filing",
}

DAYS_PER_MONTH = 30.44


@dataclass
class CompanyInputs:
    company_id: int
    name: str
    sector: str | None
    listed: bool
    values: dict[str, float | None] = field(default_factory=dict)
    collected_at: dict[str, datetime | None] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)
    news_events: list[NewsEvent] = field(default_factory=list)
    news_scanned_at: datetime | None = None


def is_listed(company: Company) -> bool:
    return bool((company.ticker or "").strip() and (company.exchange or "").strip())


def gather_inputs(db: Session, company: Company, as_of: datetime) -> CompanyInputs:
    inputs = CompanyInputs(company.id, company.canonical_name, company.sector, is_listed(company))

    for feature_key, kpi_name in KPI_FEATURES.items():
        row = db.execute(
            select(KpiObservation.value_numeric, KpiObservation.collected_at)
            .where(
                KpiObservation.company_id == company.id,
                KpiObservation.kpi_name == kpi_name,
                KpiObservation.observed_at <= as_of,
            )
            .order_by(desc(KpiObservation.observed_at), desc(KpiObservation.id))
            .limit(1)
        ).first()
        if row and row[0] is not None:
            inputs.values[feature_key] = float(row[0])
            inputs.collected_at[feature_key] = ensure_utc(row[1])

    mca_collected = latest_record_time(db, company.id, MCA_SOURCE_NAME, "mca_company_master")
    if company.incorporation_date:
        age = (as_of.date() - company.incorporation_date).days / 365.25
        inputs.values["company_age_years"] = round(age, 2)
        inputs.collected_at["company_age_years"] = mca_collected
    if company.company_status:
        overdue = company.company_status.strip().lower() in FILINGS_OVERDUE_STATUSES
        inputs.values["filings_overdue"] = 1.0 if overdue else 0.0
        inputs.collected_at["filings_overdue"] = mca_collected
        inputs.display["filings_overdue"] = company.company_status

    inputs.news_events = news_events(db, company.id, as_of)
    inputs.news_scanned_at = latest_news_scan(db, company.id, as_of)

    if not inputs.listed:
        funding = [e for e in inputs.news_events if e.signal == "funding_raised"]
        if funding:
            latest = max(e.published_at for e in funding)
            inputs.values["months_since_last_funding"] = round((as_of - latest).days / DAYS_PER_MONTH, 1)
            inputs.collected_at["months_since_last_funding"] = latest

    return inputs


def news_events(db: Session, company_id: int, as_of: datetime) -> list[NewsEvent]:
    """Classified articles about the company, one entry per story (event id)."""
    rows = db.execute(
        select(SourceRecord.url, SourceRecord.title, SourceRecord.published_at, SourceRecord.normalized_data)
        .join(DataSource, DataSource.id == SourceRecord.source_id)
        .where(
            SourceRecord.company_id == company_id,
            DataSource.name == NEWS_SIGNAL_SOURCE_NAME,
            SourceRecord.record_type == CLASSIFIED_RECORD_TYPE,
            SourceRecord.published_at.is_not(None),
            SourceRecord.published_at <= as_of,
        )
    ).all()

    reports: dict[str, list[NewsEvent]] = {}
    for url, title, published, data in rows:
        data = data or {}
        if not data.get("about_company"):
            continue
        key = data.get("event_id") or url
        reports.setdefault(key, []).append(
            NewsEvent(
                key=key,
                signal=data.get("signal") or "neutral",
                published_at=ensure_utc(published),
                label=data.get("event") or title or "News event",
                severity=int(data.get("severity") or 0),
            )
        )

    # A story dates from its first report; its severity is the highest any report gave.
    stories = []
    for key, items in reports.items():
        first = min(items, key=lambda item: item.published_at)
        severity = max(item.severity for item in items)
        stories.append(NewsEvent(key, first.signal, first.published_at, first.label, severity))
    return stories


def latest_news_scan(db: Session, company_id: int, as_of: datetime) -> datetime | None:
    started = db.scalar(
        select(CollectionRun.started_at)
        .where(
            CollectionRun.company_id == company_id,
            CollectionRun.status == "completed",
            CollectionRun.extra_metadata["source"].as_string() == NEWS_SIGNAL_SOURCE_NAME,
            CollectionRun.started_at <= as_of,
        )
        .order_by(desc(CollectionRun.started_at))
        .limit(1)
    )
    return ensure_utc(started)


def latest_record_time(db: Session, company_id: int, source_name: str, record_type: str) -> datetime | None:
    collected = db.scalar(
        select(SourceRecord.collected_at)
        .join(DataSource, DataSource.id == SourceRecord.source_id)
        .where(
            SourceRecord.company_id == company_id,
            DataSource.name == source_name,
            SourceRecord.record_type == record_type,
        )
        .order_by(desc(SourceRecord.collected_at))
        .limit(1)
    )
    return ensure_utc(collected)


def news_scanned_recently(inputs: CompanyInputs, as_of: datetime, window_days: int) -> bool:
    return inputs.news_scanned_at is not None and as_of - inputs.news_scanned_at <= timedelta(days=window_days)


def now_utc() -> datetime:
    return datetime.now(UTC)
