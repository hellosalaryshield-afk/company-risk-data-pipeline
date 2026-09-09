from datetime import datetime

from pydantic import BaseModel, Field


class CompanyCollectionRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    days_back: int = Field(default=30, ge=1, le=30)
    page_size: int = Field(default=25, ge=1, le=100)
    range_period: str = Field(default="6mo", pattern=r"^(5d|1mo|3mo|6mo|1y|2y|5y|max)$")
    include_metered_sources: bool = Field(
        default=False,
        description="Run sources that are billed per call, such as the Apify Glassdoor actor.",
    )
    include_slow_sources: bool = Field(
        default=True,
        description=(
            "Run rate-limited sources such as GDELT. Adds roughly 25 seconds. Turn off for a "
            "public-facing call and read the values the scheduled batch already stored."
        ),
    )
    gdelt_timespan: str = Field(default="3m", pattern=r"^(1w|1m|2m|3m|6m|12m|24m)$")


class SourceOutcome(BaseModel):
    source: str
    status: str
    collection_run_id: int | None = None
    records_stored: int | None = None
    record_found: bool | None = None
    match_confidence: str | None = None
    message: str | None = None
    kpis: dict[str, float] = {}


class CompanyCollectionResponse(BaseModel):
    status: str
    query: str
    confidence: str | None = None
    message: str | None = None
    candidates: list[dict] = []
    company: dict | None = None
    segment: dict | None = None
    started_at: str | None = None
    completed_at: str | None = None
    sources: list[SourceOutcome] = []
    sources_succeeded: list[str] = []
    sources_failed: list[str] = []
    sources_skipped: list[str] = []
    sources_match_rejected: list[str] = []
    kpis: dict[str, float] = {}


class MacroCollectionRequest(BaseModel):
    range_period: str = Field(default="6mo", pattern=r"^(5d|1mo|3mo|6mo|1y|2y|5y|max)$")
    interval: str = Field(default="1d", pattern=r"^(1d|1wk|1mo)$")
    keys: list[str] | None = None


class MacroCollectionResponse(BaseModel):
    status: str
    collection_run_id: int | None = None
    range: str | None = None
    indicators_collected: list[dict] = []
    indicators_failed: list[dict] = []


class DataSourceRead(BaseModel):
    id: int
    name: str
    source_type: str
    base_url: str | None = None
    description: str | None = None
    requires_auth: bool
    is_active: bool
    known_limitations: str | None = None

    model_config = {"from_attributes": True}


class CollectionRunRead(BaseModel):
    id: int
    company_id: int | None = None
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    extra_metadata: dict = {}

    model_config = {"from_attributes": True}


class CompanySummaryResponse(BaseModel):
    company: dict
    segment: dict
    applicable_sources: list[str] = []
    coverage: dict
    kpis: list[dict] = []
    peer_context: dict = {}
    source_health: list[dict] = []
    macro: list[dict] = []
    notes: list[str] = []
    generated_at: datetime


class GdeltCollectionRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    timespan: str = Field(default="3m", pattern=r"^(1w|1m|2m|3m|6m|12m|24m)$")
    max_articles: int = Field(default=25, ge=1, le=250)


class GdeltCollectionResponse(BaseModel):
    status: str
    query: str | None = None
    confidence: str | None = None
    message: str | None = None
    candidates: list[str] = []
    company: dict | None = None
    collection_run_id: int | None = None
    source: str | None = None
    record_found: bool | None = None
    records_stored: int | None = None
    gdelt_record: dict | None = None
    kpis: dict[str, float] = {}


class FeatureVectorResponse(BaseModel):
    company_id: int
    as_of: str
    safe_only: bool
    features: dict[str, float] = {}
    excluded: list[dict] = []
    provenance: list[dict] = []


class LeakageScanResponse(BaseModel):
    as_of: str
    observations_checked: int
    point_in_time_safe: int
    with_issues: int
    findings: list[dict] = []
    impossible_timestamps: list[dict] = []
