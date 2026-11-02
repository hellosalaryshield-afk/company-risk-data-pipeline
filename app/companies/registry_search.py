"""Find companies in the bulk MCA registry copy (mca_companies).

Exact matching is on normalized names, which drop legal suffixes, so "Zepto" finds
"ZEPTO LIMITED" and "Razorpay Software Private Limited" finds "RAZORPAY SOFTWARE LIMITED"
after a company converts from private to public. Fuzzy matching uses pg_trgm and only
runs on PostgreSQL.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.companies.normalization import normalize_company_name
from app.models.registry import McaCompany

ACTIVE = "Active"


@dataclass(frozen=True)
class RegistryCandidate:
    company: McaCompany
    score: float


@dataclass(frozen=True)
class RegistryMatch:
    status: str  # "matched", "ambiguous" or "unmatched"
    matched_via: str | None = None
    company: McaCompany | None = None
    candidates: list[McaCompany] = field(default_factory=list)


def compact(name: str) -> str:
    """Normalized name without spaces, so "One97" and "One 97" compare equal."""
    return normalize_company_name(name).replace(" ", "")


def find_in_registry(db: Session, names: list[str], cin: str | None = None) -> RegistryMatch:
    """Identify one company from its CIN or known names, without guessing.

    Order: CIN, then an exact normalized name, then a fuzzy candidate whose name is the
    same once spaces are removed. Several equally good matches are reported as
    ambiguous rather than picking one, though an Active company is preferred over closed
    ones with the same name.
    """
    if cin:
        company = db.get(McaCompany, cin.strip().upper())
        if company:
            return RegistryMatch("matched", "cin", company)

    normalized = [n for n in dict.fromkeys(normalize_company_name(name) for name in names if name) if n]
    if not normalized:
        return RegistryMatch("unmatched")

    for name in normalized:
        rows = list(db.scalars(select(McaCompany).where(McaCompany.normalized_name == name).limit(10)))
        match = pick_one(rows, "exact_name")
        if match.status != "unmatched":
            return match

    if not is_postgresql(db):
        return RegistryMatch("unmatched")

    for name in normalized:
        target = name.replace(" ", "")
        same = [c.company for c in fuzzy_candidates(db, name, limit=10) if compact(c.company.company_name) == target]
        match = pick_one(same, "compact_name")
        if match.status != "unmatched":
            return match

    return RegistryMatch("unmatched")


def pick_one(rows: list[McaCompany], matched_via: str) -> RegistryMatch:
    if not rows:
        return RegistryMatch("unmatched")
    active = [row for row in rows if row.company_status == ACTIVE]
    pool = active or rows
    if len(pool) == 1:
        return RegistryMatch("matched", matched_via, pool[0])
    return RegistryMatch("ambiguous", matched_via, None, pool)


def fuzzy_candidates(db: Session, query: str, limit: int = 5) -> list[RegistryCandidate]:
    """Closest registry names by trigram similarity, best first. PostgreSQL only."""
    normalized = normalize_company_name(query)
    if not normalized or not is_postgresql(db):
        return []

    score = func.similarity(McaCompany.normalized_name, normalized).label("score")
    statement = (
        select(McaCompany, score)
        .where(McaCompany.normalized_name.op("%")(normalized))
        .order_by(score.desc())
        .limit(limit)
    )
    return [RegistryCandidate(company, float(value)) for company, value in db.execute(statement).all()]


def is_postgresql(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def as_api_record(company: McaCompany) -> dict:
    """The registry row in data.gov.in field names, so it flows through normalize_mca_record."""
    return {
        "CIN": company.cin,
        "CompanyName": company.company_name,
        "CompanyStatus": company.company_status,
        "CompanyClass": company.company_class,
        "CompanyCategory": company.company_category,
        "CompanyStateCode": company.registered_state,
        "CompanyRegistrationdate_date": company.registration_date.isoformat() if company.registration_date else None,
        "Listingstatus": company.listing_status,
        "CompanyIndustrialClassification": company.industrial_classification,
    }
