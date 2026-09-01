from pydantic import BaseModel, Field


class CompanyAliasRead(BaseModel):
    alias: str
    normalized_alias: str

    model_config = {"from_attributes": True}


class CompanyRead(BaseModel):
    id: int
    canonical_name: str
    legal_name: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    website: str | None = None
    aliases: list[CompanyAliasRead] = []

    model_config = {"from_attributes": True}


class CompanyCreate(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = []
    legal_name: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    website: str | None = None


class CompanyResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=255)


class CompanyResolveResponse(BaseModel):
    status: str
    query: str
    normalized_query: str
    confidence: str
    match: CompanyRead | None = None
    candidates: list[CompanyRead] = []
