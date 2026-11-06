"""Score companies and build the monthly peer tables (Developer Instructions v1, steps 1-8)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import fmean
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.scoring import RiskScore, ScorePeerStat
from app.scoring.config import PILLARS, FeatureSpec, feature_specs, load_config
from app.scoring.engine import (
    Driver,
    band_for,
    confidence_for,
    drivers,
    feature_score,
    news_score,
    overall_score,
    pillar_score,
    top_drivers,
)
from app.scoring.inputs import CompanyInputs, gather_inputs, news_scanned_recently, now_utc

ALL_GROUP = "all"
OVERALL_KEY = "__overall__"
NEWS_KEY = "__news__"


@dataclass(frozen=True)
class PeerStat:
    values: list[float]
    average_score: float | None
    company_count: int


class PeerTables:
    """Peer values and averages per group. A company uses its sector when that sector has
    at least `min_group` companies with the data, otherwise every company (step 2)."""

    def __init__(self, stats: dict[tuple[str, str], PeerStat], min_group: int):
        self.stats = stats
        self.min_group = min_group

    def lookup(self, feature_key: str, sector: str | None) -> PeerStat | None:
        sector_stat = self.stats.get((sector_group(sector), feature_key)) if sector else None
        if sector_stat and sector_stat.company_count >= self.min_group:
            return sector_stat
        return self.stats.get((ALL_GROUP, feature_key))


def sector_group(sector: str | None) -> str:
    return f"sector:{sector}"


def all_feature_specs(config: dict[str, Any]) -> list[FeatureSpec]:
    seen: dict[str, FeatureSpec] = {}
    for listed in (True, False):
        for spec in feature_specs(config, listed):
            seen.setdefault(spec.key, spec)
    return list(seen.values())


# Monthly peer job --------------------------------------------------------------------------


def build_peer_tables(
    db: Session, as_of: datetime | None = None, config: dict[str, Any] | None = None, persist: bool = True
) -> PeerTables:
    """Peer values per feature and group, each group's average feature score (for the
    drivers), and its average news and pre-adjustment overall score (for shrinkage)."""
    config = config or load_config()
    as_of = as_of or now_utc()
    companies = list(db.scalars(select(Company).where(Company.is_active.is_(True))))
    inputs = [gather_inputs(db, company, as_of) for company in companies]
    min_values = config["min_values_for_percentile"]

    def groups_of(item: CompanyInputs) -> list[str]:
        return [ALL_GROUP] + ([sector_group(item.sector)] if item.sector else [])

    members: dict[str, list[CompanyInputs]] = {}
    for item in inputs:
        for group in groups_of(item):
            members.setdefault(group, []).append(item)

    stats: dict[tuple[str, str], PeerStat] = {}
    for spec in all_feature_specs(config):
        for group, items in members.items():
            values = sorted(i.values[spec.key] for i in items if i.values.get(spec.key) is not None)
            if not values:
                continue
            scores = [feature_score(spec, v, values, min_values) for v in values]
            scores = [s for s in scores if s is not None]
            stats[(group, spec.key)] = PeerStat(values, fmean(scores) if scores else None, len(values))

    tables = PeerTables(stats, config["min_peer_group_size"])

    # News and overall averages need each company scored against the tables just built.
    news_by_group: dict[str, list[float]] = {}
    raw_by_group: dict[str, list[float]] = {}
    for item in inputs:
        result = compute(item, tables, None, as_of, config)
        for group in groups_of(item):
            if result.news_score is not None:
                news_by_group.setdefault(group, []).append(result.news_score)
            if result.overall.raw is not None:
                raw_by_group.setdefault(group, []).append(result.overall.raw)
    for key, by_group in ((NEWS_KEY, news_by_group), (OVERALL_KEY, raw_by_group)):
        for group, values in by_group.items():
            stats[(group, key)] = PeerStat(sorted(values), fmean(values), len(values))

    if persist:
        version = config["weights_version"]
        db.execute(delete(ScorePeerStat).where(ScorePeerStat.weights_version == version))
        for (group, key), stat in stats.items():
            db.add(
                ScorePeerStat(
                    weights_version=version,
                    group_key=group,
                    feature_key=key,
                    peer_values=stat.values,
                    average_score=Decimal(str(round(stat.average_score, 4))) if stat.average_score is not None else None,
                    company_count=stat.company_count,
                )
            )
        db.commit()
    return tables


def load_peer_tables(db: Session, config: dict[str, Any]) -> PeerTables | None:
    rows = db.scalars(select(ScorePeerStat).where(ScorePeerStat.weights_version == config["weights_version"])).all()
    if not rows:
        return None
    stats = {
        (row.group_key, row.feature_key): PeerStat(
            [float(v) for v in row.peer_values],
            float(row.average_score) if row.average_score is not None else None,
            row.company_count,
        )
        for row in rows
    }
    return PeerTables(stats, config["min_peer_group_size"])


# Scoring -----------------------------------------------------------------------------------


@dataclass
class Computation:
    feature_rows: list[tuple[FeatureSpec, float | None, float | None, float | None]]
    pillars: dict[str, tuple[float | None, float]]
    news_score: float | None
    news_events: list
    overall: Any
    sector_average: float | None


def compute(
    inputs: CompanyInputs,
    tables: PeerTables,
    sector_average: float | None,
    as_of: datetime,
    config: dict[str, Any],
) -> Computation:
    min_values = config["min_values_for_percentile"]
    rows = []
    for spec in feature_specs(config, inputs.listed):
        value = inputs.values.get(spec.key)
        stat = tables.lookup(spec.key, inputs.sector)
        score = feature_score(spec, value, stat.values if stat else [], min_values)
        rows.append((spec, value, score, stat.average_score if stat else None))

    pillars: dict[str, tuple[float | None, float]] = {}
    for pillar in ("financial", "sentiment", "profile"):
        items = [(spec.weight, score) for spec, _, score, _ in rows if spec.pillar == pillar]
        score, coverage = pillar_score(items)
        if score is not None and is_stale(inputs, [s for s, _, sc, _ in rows if s.pillar == pillar and sc is not None], as_of, config):
            coverage /= 2
        pillars[pillar] = (score, coverage)

    news_value = None
    kept: list = []
    if news_scanned_recently(inputs, as_of, config["news_scan_window_days"]):
        news_value, kept = news_score(inputs.news_events, as_of, config)
        pillars["news"] = (news_value, 1.0)
    else:
        pillars["news"] = (None, 0.0)

    overall = overall_score(pillars, config["pillar_weights"], sector_average, config)
    return Computation(rows, pillars, news_value, kept, overall, sector_average)


def is_stale(inputs: CompanyInputs, specs: list[FeatureSpec], as_of: datetime, config: dict[str, Any]) -> bool:
    """A pillar is stale when its newest input is older than `stale_data_days`."""
    dates = [inputs.collected_at.get(spec.key) for spec in specs]
    dates = [d for d in dates if d is not None]
    return bool(dates) and (as_of - max(dates)).days > config["stale_data_days"]


def score_company(
    db: Session,
    company: Company,
    as_of: datetime | None = None,
    config: dict[str, Any] | None = None,
    tables: PeerTables | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    config = config or load_config()
    as_of = as_of or now_utc()
    tables = tables or load_peer_tables(db, config) or build_peer_tables(db, as_of, config, persist=False)

    inputs = gather_inputs(db, company, as_of)
    sector_stat = tables.lookup(OVERALL_KEY, inputs.sector)
    sector_average = sector_stat.average_score if sector_stat else None
    result = compute(inputs, tables, sector_average, as_of, config)
    news_stat = tables.lookup(NEWS_KEY, inputs.sector)
    output = to_output(company, inputs, result, as_of, config, news_stat.average_score if news_stat else None)

    if persist:
        db.add(
            RiskScore(
                company_id=company.id,
                weights_version=config["weights_version"],
                score=Decimal(str(output["score"])) if output["score"] is not None else None,
                band=output["band"],
                confidence=output["confidence"],
                coverage=Decimal(str(round(result.overall.coverage, 4))),
                data_as_of=max((d for d in inputs.collected_at.values() if d), default=None),
                result=output,
            )
        )
        db.commit()
    return output


def to_output(
    company: Company,
    inputs: CompanyInputs,
    result: Computation,
    as_of: datetime,
    config: dict[str, Any],
    news_peer_average: float | None = None,
) -> dict[str, Any]:
    overall = result.overall
    pillar_weights = config["pillar_weights"]
    data_dates = [d for d in inputs.collected_at.values() if d] + [e.published_at for e in inputs.news_events]

    output: dict[str, Any] = {
        "company": company.legal_name or company.canonical_name,
        "company_id": company.id,
        "status": "scored" if overall.final is not None else "not_enough_data",
        "score": round(overall.final) if overall.final is not None else None,
        "band": band_for(overall.final, config) if overall.final is not None else None,
        "confidence": confidence_for(overall.coverage, config),
        "coverage": round(overall.coverage, 3),
        "horizon": config["horizon"],
        "pillars": [
            {
                "name": pillar.capitalize(),
                "score": round(result.pillars[pillar][0]) if result.pillars[pillar][0] is not None else None,
                "weight": pillar_weights[pillar],
                "coverage": round(result.pillars[pillar][1], 2),
            }
            for pillar in PILLARS
        ],
        "sector_average": round(result.sector_average) if result.sector_average is not None else None,
        "drivers": [],
        "weights_version": config["weights_version"],
        "data_as_of": max(data_dates).date().isoformat() if data_dates else None,
        "scored_at": as_of.isoformat(),
        "inputs": {spec.key: value for spec, value, _, _ in result.feature_rows if value is not None},
        "news_events": [
            {
                "label": item.event.label,
                "type": item.event.signal,
                "date": item.event.published_at.date().isoformat(),
                "points": item.points,
                "decayed_points": round(item.decayed_points, 2),
            }
            for item in result.news_events
        ],
    }

    if overall.raw is None:
        return output

    feature_rows = [
        (spec, score, avg if avg is not None else score, driver_label(spec, value, inputs))
        for spec, value, score, avg in result.feature_rows
        if score is not None
    ]
    available_weight = {}
    for spec, *_ in feature_rows:
        available_weight[spec.pillar] = available_weight.get(spec.pillar, 0.0) + spec.weight
    news = None
    if result.news_score is not None:
        peer_news = news_peer_average if news_peer_average is not None else result.news_score
        news = (result.news_score, peer_news, result.news_events)
    baseline, items = drivers(feature_rows, available_weight, overall.effective_weights, news)

    adjustment = overall.final - overall.raw
    if abs(adjustment) >= 0.5:
        items.append(Driver("Limited data adjustment", "adjustment", adjustment))
    chosen = top_drivers(items, config["drivers"]["top_raising"], config["drivers"]["top_lowering"])
    output["drivers"] = [
        {"label": d.label, "pillar": d.pillar.capitalize(), "points": round(d.points)}
        for d in chosen
        if round(d.points) != 0
    ]
    output["baseline"] = round(baseline, 2)
    output["score_before_adjustment"] = round(overall.raw, 2)
    return output


def driver_label(spec: FeatureSpec, value: float | None, inputs: CompanyInputs) -> str:
    if value is None:
        return spec.label
    key = spec.key
    if key == "share_price_change_6m":
        return f"Share price {'down' if value < 0 else 'up'} {abs(value):.0f}% in 6 months"
    if key in {"positive_outlook_share", "ceo_approval_share", "recommend_share"}:
        return f"{spec.label}: {value * 100:.0f}%"
    if key in {"glassdoor_rating", "senior_management_rating"}:
        return f"{spec.label} {value:.1f} of 5"
    if key == "company_age_years":
        return f"Registered {value:.0f} years ago"
    if key == "filings_overdue":
        status = inputs.display.get("filings_overdue", "")
        return f"Filings overdue (MCA status: {status})" if value >= 1 else f"Filings up to date (MCA status: {status})"
    if key == "months_since_last_funding":
        return f"{value:.0f} months since last funding round"
    return f"{spec.label}: {value:g}"
