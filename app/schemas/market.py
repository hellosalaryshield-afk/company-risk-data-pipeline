from pydantic import BaseModel, Field


class MarketCollectionRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    range_period: str = Field(default="6mo", pattern=r"^(5d|1mo|3mo|6mo|1y|2y|5y|max)$")
    interval: str = Field(default="1d", pattern=r"^(1d|1wk|1mo)$")


class MarketCollectionResponse(BaseModel):
    status: str
    query: str | None = None
    confidence: str | None = None
    message: str | None = None
    candidates: list[str] = []
    company: dict | None = None
    collection_run_id: int | None = None
    source: str | None = None
    symbol: str | None = None
    record_found: bool | None = None
    market_record: dict | None = None
    kpis: dict[str, float] = {}


class CompanySegmentRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)


class CompanySegmentResponse(BaseModel):
    status: str
    query: str
    confidence: str
    message: str | None = None
    candidates: list[str] = []
    company: dict | None = None
    segment: str | None = None
    geography: str | None = None
    listing_status: str | None = None
    funding_status: str | None = None
    reasons: list[str] = []
