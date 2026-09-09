from pydantic import BaseModel, Field


class WorkplaceCollectionRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    domain: str = Field(
        default="www.glassdoor.co.in",
        description="Glassdoor domain to search. Use www.glassdoor.com for non-India companies.",
    )


class WorkplaceCollectionResponse(BaseModel):
    status: str
    query: str | None = None
    confidence: str | None = None
    message: str | None = None
    candidates: list[str] = []
    company: dict | None = None
    collection_run_id: int | None = None
    source: str | None = None
    record_found: bool | None = None
    match_confidence: str | None = None
    workplace_record: dict | None = None
    kpis: dict[str, float] = {}
