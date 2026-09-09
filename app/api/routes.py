from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.companies.normalization import normalize_company_name
from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.models.collection import CollectionRun
from app.models.source import DataSource
from app.pipeline.company_collection import collect_company_data
from app.pipeline.gdelt_collection import collect_gdelt_for_company
from app.pipeline.macro_collection import collect_macro_indicators
from app.pipeline.market_collection import collect_market_for_company
from app.pipeline.mca_collection import McaCollectionError, collect_mca_for_company
from app.pipeline.news_collection import NewsCollectionError, collect_news_for_company
from app.schemas.collection import NewsCollectionRequest, NewsCollectionResponse
from app.schemas.company import CompanyCreate, CompanyRead, CompanyResolveRequest, CompanyResolveResponse
from app.schemas.market import (
    CompanySegmentRequest,
    CompanySegmentResponse,
    MarketCollectionRequest,
    MarketCollectionResponse,
)
from app.schemas.mca import McaCollectionRequest, McaCollectionResponse
from app.schemas.workplace import WorkplaceCollectionRequest, WorkplaceCollectionResponse
from app.schemas.pipeline import (
    CollectionRunRead,
    GdeltCollectionRequest,
    GdeltCollectionResponse,
    CompanyCollectionRequest,
    CompanyCollectionResponse,
    CompanySummaryResponse,
    DataSourceRead,
    MacroCollectionRequest,
    MacroCollectionResponse,
)
from app.pipeline.workplace_collection import WorkplaceCollectionError, collect_workplace_for_company
from app.reports.company_report import build_company_report
from app.reports.renderer import render_company_report_html, render_company_report_pdf

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
        company_segment=payload.company_segment,
        funding_status=payload.funding_status,
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


@router.post("/companies/segment", response_model=CompanySegmentResponse)
def classify_company_segment(payload: CompanySegmentRequest, db: Session = Depends(get_db_session)) -> CompanySegmentResponse:
    repository = CompanyRepository(db)
    resolved = CompanyResolver(repository).resolve(payload.company_name)
    if not resolved.match:
        return CompanySegmentResponse(
            status=resolved.status,
            query=payload.company_name,
            confidence=resolved.confidence,
            message="Company could not be resolved. Pick a candidate or add it to the registry first.",
            candidates=[candidate.canonical_name for candidate in resolved.candidates],
        )

    classification = repository.classify_segment(resolved.match)
    db.commit()

    return CompanySegmentResponse(
        status="classified" if classification.segment else "undetermined",
        query=payload.company_name,
        confidence=classification.confidence,
        company={"id": resolved.match.id, "canonical_name": resolved.match.canonical_name},
        segment=classification.segment,
        geography=classification.geography,
        listing_status=classification.listing_status,
        funding_status=classification.funding_status,
        reasons=classification.reasons,
    )


@router.post("/collections/market", response_model=MarketCollectionResponse)
def collect_market(payload: MarketCollectionRequest, db: Session = Depends(get_db_session)) -> MarketCollectionResponse:
    result = collect_market_for_company(
        db=db,
        query=payload.company_name,
        range_period=payload.range_period,
        interval=payload.interval,
    )
    return MarketCollectionResponse(**result)


@router.post("/collections/company", response_model=CompanyCollectionResponse)
def collect_company(payload: CompanyCollectionRequest, db: Session = Depends(get_db_session)) -> CompanyCollectionResponse:
    """Run every source that applies to this company's segment, in one call."""
    result = collect_company_data(
        db=db,
        query=payload.company_name,
        settings=get_settings(),
        days_back=payload.days_back,
        page_size=payload.page_size,
        range_period=payload.range_period,
        include_metered_sources=payload.include_metered_sources,
    )
    return CompanyCollectionResponse(**result)


@router.post("/collections/macro", response_model=MacroCollectionResponse)
def collect_macro(payload: MacroCollectionRequest, db: Session = Depends(get_db_session)) -> MacroCollectionResponse:
    """Collect macro and index covariates. These are company-independent."""
    result = collect_macro_indicators(
        db=db,
        range_period=payload.range_period,
        interval=payload.interval,
        keys=payload.keys,
    )
    return MacroCollectionResponse(**result)


@router.get("/sources", response_model=list[DataSourceRead])
def list_sources(db: Session = Depends(get_db_session)) -> list[DataSourceRead]:
    return list(db.scalars(select(DataSource).order_by(DataSource.name)).all())


@router.get("/collection-runs", response_model=list[CollectionRunRead])
def list_collection_runs(
    limit: int = 25,
    company_id: int | None = None,
    db: Session = Depends(get_db_session),
) -> list[CollectionRunRead]:
    statement = select(CollectionRun).order_by(desc(CollectionRun.started_at)).limit(min(limit, 100))
    if company_id is not None:
        statement = statement.where(CollectionRun.company_id == company_id)
    return list(db.scalars(statement).all())


def load_company_or_404(company_id: int, db: Session):
    company = CompanyRepository(db).get_company(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")
    return company


@router.get("/companies/{company_id}/summary", response_model=CompanySummaryResponse)
def company_summary(company_id: int, db: Session = Depends(get_db_session)) -> CompanySummaryResponse:
    report = build_company_report(db, load_company_or_404(company_id, db))
    return CompanySummaryResponse(**report)


@router.get("/companies/{company_id}/report", response_class=HTMLResponse)
def company_report_html(company_id: int, db: Session = Depends(get_db_session)) -> HTMLResponse:
    report = build_company_report(db, load_company_or_404(company_id, db))
    html = render_company_report_html(report, footer_note=get_settings().report_footer_note)
    return HTMLResponse(content=html)


@router.get("/companies/{company_id}/report.pdf")
def company_report_pdf(company_id: int, db: Session = Depends(get_db_session)) -> Response:
    company = load_company_or_404(company_id, db)
    report = build_company_report(db, company)
    pdf = render_company_report_pdf(report, footer_note=get_settings().report_footer_note)
    filename = company.canonical_name.replace(" ", "_").lower()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}_signal_report.pdf"'},
    )


@router.post("/collections/workplace", response_model=WorkplaceCollectionResponse)
def collect_workplace(
    payload: WorkplaceCollectionRequest, db: Session = Depends(get_db_session)
) -> WorkplaceCollectionResponse:
    """Collect Glassdoor workplace ratings and open job count. Billed per call."""
    try:
        result = collect_workplace_for_company(
            db=db,
            query=payload.company_name,
            settings=get_settings(),
            domain=payload.domain,
        )
    except WorkplaceCollectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return WorkplaceCollectionResponse(**result)


@router.post("/collections/gdelt", response_model=GdeltCollectionResponse)
def collect_gdelt(payload: GdeltCollectionRequest, db: Session = Depends(get_db_session)) -> GdeltCollectionResponse:
    """Collect GDELT news tone and coverage volume for one company. No API key required."""
    result = collect_gdelt_for_company(
        db=db,
        query=payload.company_name,
        timespan=payload.timespan,
        max_articles=payload.max_articles,
    )
    return GdeltCollectionResponse(**result)
