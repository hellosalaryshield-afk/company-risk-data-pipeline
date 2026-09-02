from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.companies.normalization import normalize_company_name
from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.pipeline.mca_collection import McaCollectionError, collect_mca_for_company
from app.pipeline.news_collection import NewsCollectionError, collect_news_for_company
from app.schemas.collection import NewsCollectionRequest, NewsCollectionResponse
from app.schemas.company import CompanyCreate, CompanyRead, CompanyResolveRequest, CompanyResolveResponse
from app.schemas.mca import McaCollectionRequest, McaCollectionResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "app_env": settings.app_env,
    }


@router.get("/companies", response_model=list[CompanyRead])
def list_companies(db: Session = Depends(get_db_session)) -> list[CompanyRead]:
    return CompanyRepository(db).list_companies()


@router.post("/companies", response_model=CompanyRead, status_code=201)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db_session)) -> CompanyRead:
    repository = CompanyRepository(db)
    existing = repository.get_by_normalized_alias(normalize_company_name(payload.canonical_name))
    if existing:
        raise HTTPException(status_code=409, detail="Company already exists.")

    return repository.create_company(
        canonical_name=payload.canonical_name,
        aliases=payload.aliases,
        legal_name=payload.legal_name,
        country=payload.country,
        sector=payload.sector,
        industry=payload.industry,
        cin=payload.cin,
        incorporation_date=payload.incorporation_date,
        company_status=payload.company_status,
        company_category=payload.company_category,
        ticker=payload.ticker,
        exchange=payload.exchange,
        website=payload.website,
    )


@router.post("/companies/resolve", response_model=CompanyResolveResponse)
def resolve_company(payload: CompanyResolveRequest, db: Session = Depends(get_db_session)) -> CompanyResolveResponse:
    resolver = CompanyResolver(CompanyRepository(db))
    result = resolver.resolve(payload.query)
    return CompanyResolveResponse(
        status=result.status,
        query=result.query,
        normalized_query=result.normalized_query,
        confidence=result.confidence,
        match=result.match,
        candidates=result.candidates,
    )


@router.post("/collections/news", response_model=NewsCollectionResponse)
def collect_news(payload: NewsCollectionRequest, db: Session = Depends(get_db_session)) -> NewsCollectionResponse:
    try:
        result = collect_news_for_company(
            db=db,
            query=payload.company_name,
            settings=get_settings(),
            days_back=payload.days_back,
            page_size=payload.page_size,
        )
    except NewsCollectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return NewsCollectionResponse(**result)


@router.post("/collections/mca", response_model=McaCollectionResponse)
def collect_mca(payload: McaCollectionRequest, db: Session = Depends(get_db_session)) -> McaCollectionResponse:
    try:
        result = collect_mca_for_company(
            db=db,
            query=payload.company_name,
            settings=get_settings(),
        )
    except McaCollectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return McaCollectionResponse(**result)
