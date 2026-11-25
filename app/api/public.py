"""The API the Salary Shield website calls for its "Is your company at risk?" section.

Only what the page shows is returned: no inputs, peer tables or raw records. The full
scorecard is never downloadable from here; it is emailed, so each request is a lead.
"""

import difflib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.companies.directory_lookup import DirectoryIndex, onboard
from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.models.company import Company, CompanyAlias
from app.models.directory import DirectoryCompany
from app.models.lead import ReportRequest
from app.models.scoring import RiskScore
from app.pipeline.freshness import refresh_company
from app.reports.emailer import EmailNotSent, deliver, scorecard_email
from app.reports.scorecard import build_scorecard
from app.reports.scorecard_render import ScorecardRenderError, render_scorecard_html, render_scorecard_pdf
from app.scoring.service import score_company

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/public", tags=["website"])

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
MAX_REQUESTS_PER_EMAIL_PER_DAY = 5


def company_card(company: Company, score: RiskScore | None) -> dict[str, Any]:
    return {
        "id": company.id,
        "name": company.canonical_name,
        "sector": company.sector,
        "score": round(float(score.score)) if score and score.score is not None else None,
        "band": score.band if score else None,
        "confidence": score.confidence if score else None,
    }


def latest_scores(db: Session) -> dict[int, RiskScore]:
    newest = (
        select(RiskScore.company_id, func.max(RiskScore.computed_at).label("at"))
        .group_by(RiskScore.company_id)
        .subquery()
    )
    rows = db.scalars(
        select(RiskScore).join(
            newest, (RiskScore.company_id == newest.c.company_id) & (RiskScore.computed_at == newest.c.at)
        )
    )
    return {row.company_id: row for row in rows}


@router.get("/companies/featured")
def featured_companies(limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db_session)) -> list[dict]:
    """Scored companies for the "Recently checked" list, highest score first. Database only."""
    scores = latest_scores(db)
    companies = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(scores)))}
    ranked = sorted(
        (s for s in scores.values() if s.score is not None and s.company_id in companies),
        key=lambda s: (-float(s.score), companies[s.company_id].canonical_name),
    )
    return [company_card(companies[s.company_id], s) for s in ranked[:limit]]


@router.get("/companies/search")
def search_companies(q: str = Query(..., min_length=1, max_length=120), db: Session = Depends(get_db_session)) -> dict:
    """Find a company by name, brand or ticker. A typo gets close matches instead of nothing.

    Companies already scored come first; then the company directory (~13,000 companies from
    the taxonomy sheet). A directory company is turned into a scorable company when it is
    returned, and gets its data and score when its summary is opened.
    """
    resolved = CompanyResolver(CompanyRepository(db)).resolve(q)
    scores = latest_scores(db)
    if resolved.match:
        return {"status": "matched", "query": q, "match": company_card(resolved.match, scores.get(resolved.match.id)), "suggestions": []}

    hits, exact = DirectoryIndex.get(db).search(q)
    if hits and (exact or len(hits) == 1):
        company = onboard(db, db.get(DirectoryCompany, hits[0].id))
        return {"status": "matched", "query": q, "match": company_card(company, scores.get(company.id)), "suggestions": []}

    # Directory suggestions come in its order (listed, bigger, better-covered first), so
    # "sun pharma" offers Sun Pharmaceutical Industries before Sun Pharma Advanced Research.
    candidates: list[Company] = []
    for hit in hits:
        company = onboard(db, db.get(DirectoryCompany, hit.id))
        if all(c.id != company.id for c in candidates):
            candidates.append(company)
    candidates += [c for c in resolved.candidates if all(c.id != x.id for x in candidates)]
    if not candidates and resolved.normalized_query:
        aliases = db.execute(select(CompanyAlias.normalized_alias, CompanyAlias.company_id)).all()
        by_alias = {alias: company_id for alias, company_id in aliases}
        close = difflib.get_close_matches(resolved.normalized_query, list(by_alias), n=5, cutoff=0.6)
        ids = list(dict.fromkeys(by_alias[alias] for alias in close))
        found = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(ids)))}
        candidates = [found[i] for i in ids if i in found]

    return {
        "status": "suggestions" if candidates else "not_found",
        "query": q,
        "match": None,
        "suggestions": [company_card(c, scores.get(c.id)) for c in candidates[:5]],
    }


def summary_view(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": view["id"],
        "name": view["name"],
        "subtitle": view["subtitle"],
        "scored": view["scored"],
        "score": view["score"],
        "band": view["band"],
        "band_range": view["band_range"],
        "band_index": view["band_index"],
        "confidence": view["confidence"],
        "confidence_cells": view["confidence_cells"],
        "coverage_pct": view["coverage_pct"],
        "sector_average": view["sector_average"],
        "plain_words": view["plain_words"],
        "data_as_of": view["data_as_of"],
        "sources": [{"label": row["label"], "date": row["date"]} for row in view["sources"] if row["date"]],
    }


@router.get("/companies/{company_id}/summary")
def company_summary(
    company_id: int,
    refresh: bool = Query(True, description="Fetch stale or missing data first (can take ~30 s)"),
    db: Session = Depends(get_db_session),
) -> dict:
    """What the scorecard panel on the home page shows for one company."""
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")
    if refresh:
        refresh_company(db, company, get_settings())
    view = build_scorecard(db, company)
    view["id"] = company.id

    # Keep the "Recently checked" list in step with what the panel shows.
    stored = latest_scores(db).get(company.id)
    stored_score = round(float(stored.score)) if stored and stored.score is not None else None
    if stored is None or stored_score != view["score"]:
        score_company(db, company, persist=True)
        db.commit()
    return summary_view(view)


class ReportRequestIn(BaseModel):
    company_id: int
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    consent: bool
    website: str = ""  # hidden field; people leave it empty, form-filling bots do not


def send_report(db: Session, request_id: int) -> None:
    """Build the PDF for one report request and email it. Runs after the response is sent."""
    request = db.get(ReportRequest, request_id)
    if request is None or request.status in {"sent", "saved"}:
        return
    request.attempts += 1
    settings = get_settings()
    try:
        company = db.get(Company, request.company_id) if request.company_id else None
        if company is None:
            raise ValueError("The company no longer exists.")
        view = build_scorecard(db, company)
        pdf = render_scorecard_pdf(render_scorecard_html(view, "night"))
        message = scorecard_email(request.name, request.email, company.canonical_name, pdf, settings)
        request.status = deliver(message, settings)
        request.sent_at = datetime.now(UTC)
        request.error = None
    except (EmailNotSent, ScorecardRenderError, ValueError) as exc:
        request.status = "failed"
        request.error = str(exc)
        logger.warning("Report request %s failed: %s", request_id, exc)
    db.commit()


def report_sender() -> Callable[[int], None]:
    """Run `send_report` in its own session; the request's session is closed by then."""

    def run(request_id: int) -> None:
        db = next(get_db_session())
        try:
            send_report(db, request_id)
        finally:
            db.close()

    return run


@router.post("/report-requests", status_code=202)
def request_report(
    payload: ReportRequestIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db_session),
    sender: Callable[[int], None] = Depends(report_sender),
) -> dict:
    """Save the lead and email the full scorecard PDF in the background."""
    if payload.website:
        return {"status": "queued"}  # quietly drop bots
    email = payload.email.strip().lower()
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=422, detail="Please enter a valid email address.")
    if not payload.consent:
        raise HTTPException(status_code=422, detail="Please agree so we can email you the report.")
    company = db.get(Company, payload.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")

    since = datetime.now(UTC) - timedelta(days=1)
    recent = db.scalar(
        select(func.count()).select_from(ReportRequest).where(ReportRequest.email == email, ReportRequest.created_at >= since)
    )
    if recent >= MAX_REQUESTS_PER_EMAIL_PER_DAY:
        raise HTTPException(status_code=429, detail="You've asked for several reports today. Please try again tomorrow.")

    request = ReportRequest(
        name=payload.name.strip(), email=email, company_id=company.id,
        company_name=company.canonical_name, consent=True, status="pending",
    )
    db.add(request)
    db.commit()
    background.add_task(sender, request.id)
    return {"status": "queued", "company": company.canonical_name, "email": email}
