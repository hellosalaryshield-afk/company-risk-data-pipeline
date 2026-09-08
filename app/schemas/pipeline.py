from datetime import datetime

from pydantic import BaseModel, Field


class CompanyCollectionRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    days_back: int = Field(default=30, ge=1, le=30)
    page_size: int = Field(default=25, ge=1, le=100)
    range_period: str = Field(default="6mo", pattern=r"^(5d|1mo|3mo|6mo|1y|2y|5y|max)$")


class SourceOutcome(BaseModel):
    source: str
    status: str
    collection_run_id: int | None = None
    records_stored: int | None = None
    record_found: bool | None = None
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
    source_health: list[dict] = []
    macro: list[dict] = []
    notes: list[str] = []
    generated_at: datetime
