from statistics import median

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.models.collection import KpiObservation
from app.models.company import Company

# Phase 6 of the brief asks for "a fallback for thin-data companies - blend toward the
# sector/cohort estimate rather than returning nothing". A company with no collected
# signals of its own is the common case early in the pilot, and an empty report is the
# worst possible answer for a user who typed a real company name.
#
# The cohort is narrowed as far as the data allows, and every borrowed figure is labelled
# with which cohort it came from and how many peers stood behind it, so a sector estimate
# can never be mistaken for the company's own measurement.
MIN_PEERS = 2


def latest_kpi_per_company(db: Session, company_ids: list[int]) -> dict[int, dict[str, float]]:
    """Newest value of each KPI for each of the given companies."""
    if not company_ids:
        return {}

    rows = db.execute(
        select(
            KpiObservation.company_id,
            KpiObservation.kpi_name,
            KpiObservation.value_numeric,
        )
        .where(KpiObservation.company_id.in_(company_ids))
        .order_by(
            KpiObservation.company_id,
            KpiObservation.kpi_name,
            desc(KpiObservation.observed_at),
            desc(KpiObservation.id),
        )
    ).all()

    latest: dict[int, dict[str, float]] = {}
    for company_id, kpi_name, value in rows:
        if value is None:
            continue
        per_company = latest.setdefault(company_id, {})
        # Rows arrive newest-first per (company, kpi), so the first one wins.
        per_company.setdefault(kpi_name, float(value))

    return latest


def candidate_cohorts(db: Session, company: Company, segment: str | None) -> list[tuple[list[Company], str]]:
    """Peer groups to try, tightest first.

    Membership alone is not enough: a sector can hold three companies and still have only
    one with any collected data. So each candidate is offered in turn and the caller keeps
    the first that actually yields estimates.
    """
    everyone = [
        peer
        for peer in CompanyRepository(db).list_companies()
        if peer.id != company.id and peer.is_active
    ]

    candidates: list[tuple[list[Company], str]] = []

    if company.sector:
        same_sector = [peer for peer in everyone if peer.sector == company.sector]
        if same_sector:
            candidates.append((same_sector, f"sector: {company.sector}"))

    if segment:
        same_segment = [peer for peer in everyone if peer.company_segment == segment]
        if same_segment:
            candidates.append((same_segment, f"segment: {segment}"))

    if everyone:
        candidates.append((everyone, "all companies in the registry"))

    return candidates


def estimates_from_cohort(
    db: Session, cohort: list[Company], own_kpi_names: set[str]
) -> tuple[list[dict], int]:
    peer_values = latest_kpi_per_company(db, [peer.id for peer in cohort])

    by_kpi: dict[str, list[float]] = {}
    for values in peer_values.values():
        for kpi_name, value in values.items():
            if kpi_name in own_kpi_names:
                continue
            by_kpi.setdefault(kpi_name, []).append(value)

    estimates = [
        {
            "kpi_name": kpi_name,
            "median": round(median(values), 4),
            "peer_count": len(values),
        }
        for kpi_name, values in by_kpi.items()
        if len(values) >= MIN_PEERS
    ]
    estimates.sort(key=lambda item: item["kpi_name"])
    return estimates, len(peer_values)


def build_peer_context(
    db: Session,
    company: Company,
    segment: str | None,
    own_kpi_names: set[str],
) -> dict:
    """Sector-level medians for the signals this company is missing.

    Only KPIs the company does not have itself are returned. A company with its own
    measurement never has it overridden by a cohort estimate.
    """
    for cohort, cohort_label in candidate_cohorts(db, company, segment):
        estimates, peer_count = estimates_from_cohort(db, cohort, own_kpi_names)
        if not estimates:
            continue
        return {
            "cohort": cohort_label,
            "peer_count": peer_count,
            "estimates": estimates,
            "note": (
                f"These are median values from {peer_count} comparable companies "
                f"({cohort_label}). They describe the cohort, not this company."
            ),
        }

    return {
        "cohort": "no cohort available",
        "peer_count": 0,
        "estimates": [],
        "note": "No comparable company in the registry has collected data to estimate from yet.",
    }
