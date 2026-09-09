from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.collection import KpiObservation

# Phase 3 of the brief requires that every feature be usable at prediction time:
# "what would actually have been published and known as of the prediction date, not a
# later-revised figure", plus "a leakage check run against the final feature set before any
# modeling starts".
#
# Three separate timestamps decide whether a value is usable at a date D:
#
#   published_at   when the fact became public. Must be <= D or the value is a leak.
#   period_end     the last day the value was computed over. If it runs past D, the number
#                  itself contains the future even though it was published earlier.
#   collected_at   when this pipeline fetched it. Often after D because collection is
#                  retrospective. That is acceptable only for sources that never revise.
#
# Sources known to restate earlier figures. A backfilled value from one of these cannot be
# trusted at D, because what we hold today may not be what was published then.
REVISING_SOURCES = frozenset(
    {
        "data_gov_mca_company_master",  # MCA filings are amended and restated
    }
)

# Sources that return an undated snapshot. There is no publication date to check, so nothing
# from them can be proven point-in-time safe.
UNDATED_SOURCES = frozenset({"apify_glassdoor_company_search"})


@dataclass
class AsOfObservation:
    kpi_name: str
    value: float
    source_name: str | None
    published_at: datetime | None
    collected_at: datetime | None
    period_start: datetime | None
    period_end: datetime | None
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def backfilled_at_as_of(self) -> bool:
        return any("backfilled" in note for note in self.issues + self.warnings)

    @property
    def point_in_time_safe(self) -> bool:
        """Only blocking issues make a value unusable.

        Backfill from a source that never revises is recorded as a warning, not an issue.
        Every value this pipeline holds was collected after any past prediction date, so
        treating backfill itself as disqualifying would empty every historical vector and
        make backtesting impossible.
        """
        return not self.issues

    def knowledge_lag_days(self) -> int | None:
        if self.published_at is None or self.collected_at is None:
            return None
        return (self.collected_at - self.published_at).days


def evaluate_observation(
    kpi_name: str,
    value: float,
    source_name: str | None,
    published_at: datetime | None,
    collected_at: datetime | None,
    period_start: datetime | None,
    period_end: datetime | None,
    as_of: datetime,
) -> AsOfObservation:
    """Decide whether one stored value could honestly have been used at `as_of`."""
    issues: list[str] = []
    warnings: list[str] = []

    if published_at is None:
        issues.append("no publication date recorded, so it cannot be verified as known at the prediction date")
    elif published_at > as_of:
        issues.append("published after the prediction date")

    if period_end is not None and period_end > as_of:
        issues.append("computation window extends past the prediction date")

    if collected_at is not None and collected_at > as_of:
        if source_name in REVISING_SOURCES:
            issues.append("backfilled from a source that revises earlier figures")
        else:
            # Not a leak: the value was published before the prediction date and this source
            # does not restate, so today's copy is what would have been seen then. Recorded
            # as a warning so a reviewer can still see it was backfilled.
            warnings.append("backfilled after the prediction date (source does not revise)")

    if source_name in UNDATED_SOURCES:
        issues.append("source returns an undated snapshot")

    return AsOfObservation(
        kpi_name=kpi_name,
        value=value,
        source_name=source_name,
        published_at=published_at,
        collected_at=collected_at,
        period_start=period_start,
        period_end=period_end,
        issues=issues,
        warnings=warnings,
    )


def kpi_as_of(db: Session, company_id: int, as_of: datetime) -> list[AsOfObservation]:
    """Latest value of each KPI that was already published at `as_of`.

    Values published after `as_of` are excluded outright. Everything else is returned with
    the reasons it may still be unsafe, so the caller decides rather than the query hiding
    the problem.
    """
    from app.models.source import DataSource

    rows = db.execute(
        select(
            KpiObservation.kpi_name,
            KpiObservation.value_numeric,
            DataSource.name,
            KpiObservation.published_at,
            KpiObservation.collected_at,
            KpiObservation.period_start,
            KpiObservation.period_end,
        )
        .join(DataSource, DataSource.id == KpiObservation.source_id)
        .where(KpiObservation.company_id == company_id)
        .order_by(
            KpiObservation.kpi_name,
            desc(KpiObservation.published_at),
            desc(KpiObservation.id),
        )
    ).all()

    seen: set[str] = set()
    results: list[AsOfObservation] = []

    for kpi_name, value, source_name, published_at, collected_at, period_start, period_end in rows:
        if value is None:
            continue

        published_at = ensure_utc(published_at)
        if published_at is not None and published_at > as_of:
            # Rows are newest-first, so a later row for this KPI may still qualify.
            continue

        if kpi_name in seen:
            continue
        seen.add(kpi_name)

        results.append(
            evaluate_observation(
                kpi_name=kpi_name,
                value=float(value),
                source_name=source_name,
                published_at=published_at,
                collected_at=ensure_utc(collected_at),
                period_start=ensure_utc(period_start),
                period_end=ensure_utc(period_end),
                as_of=as_of,
            )
        )

    results.sort(key=lambda item: item.kpi_name)
    return results


def feature_vector_as_of(
    db: Session,
    company_id: int,
    as_of: datetime,
    safe_only: bool = True,
) -> dict:
    """Flat feature vector for one company at one date.

    `safe_only` defaults to True so the modelling path cannot accidentally consume a value
    that failed a point-in-time check. Set it to False only to inspect what is being dropped.
    """
    observations = kpi_as_of(db, company_id, as_of)
    included = [obs for obs in observations if obs.point_in_time_safe or not safe_only]

    return {
        "company_id": company_id,
        "as_of": as_of.isoformat(),
        "safe_only": safe_only,
        "features": {obs.kpi_name: obs.value for obs in included},
        "excluded": [
            {"kpi_name": obs.kpi_name, "issues": obs.issues}
            for obs in observations
            if not obs.point_in_time_safe and safe_only
        ],
        "provenance": [
            {
                "kpi_name": obs.kpi_name,
                "source": obs.source_name,
                "published_at": obs.published_at.isoformat() if obs.published_at else None,
                "knowledge_lag_days": obs.knowledge_lag_days(),
                "backfilled": obs.backfilled_at_as_of,
                "issues": obs.issues,
                "warnings": obs.warnings,
            }
            for obs in included
        ],
    }


def leakage_scan(db: Session, as_of: datetime | None = None) -> dict:
    """Check every stored KPI for point-in-time problems.

    The brief requires this to be run against the final feature set before modelling starts,
    with mentor sign-off. It reports by issue so a whole source's worth of problems shows up
    as one line rather than thousands.
    """
    from app.models.source import DataSource

    as_of = as_of or datetime.now(UTC)

    rows = db.execute(
        select(
            KpiObservation.company_id,
            KpiObservation.kpi_name,
            KpiObservation.value_numeric,
            DataSource.name,
            KpiObservation.published_at,
            KpiObservation.collected_at,
            KpiObservation.period_start,
            KpiObservation.period_end,
        ).join(DataSource, DataSource.id == KpiObservation.source_id)
    ).all()

    total = 0
    safe = 0
    by_issue: dict[str, dict] = {}
    impossible: list[dict] = []

    for company_id, kpi_name, value, source_name, published_at, collected_at, period_start, period_end in rows:
        if value is None:
            continue
        total += 1

        published_at = ensure_utc(published_at)
        collected_at = ensure_utc(collected_at)

        # A value cannot have been collected before it was published. If that appears, the
        # timestamps themselves are wrong and nothing downstream can be trusted.
        if published_at and collected_at and collected_at < published_at:
            impossible.append(
                {
                    "company_id": company_id,
                    "kpi_name": kpi_name,
                    "source": source_name,
                    "published_at": published_at.isoformat(),
                    "collected_at": collected_at.isoformat(),
                }
            )

        observation = evaluate_observation(
            kpi_name=kpi_name,
            value=float(value),
            source_name=source_name,
            published_at=published_at,
            collected_at=collected_at,
            period_start=ensure_utc(period_start),
            period_end=ensure_utc(period_end),
            as_of=as_of,
        )

        if observation.point_in_time_safe:
            safe += 1
            continue

        for issue in observation.issues:
            entry = by_issue.setdefault(
                issue, {"issue": issue, "count": 0, "sources": set(), "kpis": set()}
            )
            entry["count"] += 1
            entry["sources"].add(source_name)
            entry["kpis"].add(kpi_name)

    findings = sorted(
        (
            {
                "issue": entry["issue"],
                "count": entry["count"],
                "sources": sorted(entry["sources"]),
                "kpi_sample": sorted(entry["kpis"])[:6],
                "kpis_affected": len(entry["kpis"]),
            }
            for entry in by_issue.values()
        ),
        key=lambda item: item["count"],
        reverse=True,
    )

    return {
        "as_of": as_of.isoformat(),
        "observations_checked": total,
        "point_in_time_safe": safe,
        "with_issues": total - safe,
        "findings": findings,
        "impossible_timestamps": impossible,
    }


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; comparisons need them tz-aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
