from pydantic import BaseModel, Field


class NewsCollectionRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    days_back: int = Field(default=30, ge=1, le=30)
    page_size: int = Field(default=25, ge=1, le=100)


class NewsCollectionResponse(BaseModel):
    status: str
    query: str | None = None
    confidence: str | None = None
    message: str | None = None
    candidates: list[str] = []
    company: dict | None = None
    collection_run_id: int | None = None
    source: str | None = None
    records_stored: int | None = None
    kpis: dict[str, int] | None = None
