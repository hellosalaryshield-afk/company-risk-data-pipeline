from pydantic import BaseModel, Field


class McaCollectionRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)


class McaCollectionResponse(BaseModel):
    status: str
    query: str | None = None
    confidence: str | None = None
    message: str | None = None
    candidates: list[str] = []
    company: dict | None = None
    collection_run_id: int | None = None
    source: str | None = None
    record_found: bool | None = None
    mca_record: dict | None = None
