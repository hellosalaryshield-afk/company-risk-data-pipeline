"""The Version 1 weighted scorecard as pure arithmetic, with no database access.

Follows "Layoff Risk Score: Developer Instructions (Version 1)". Every function here is
deterministic, so the worked examples in section 9 can be tested exactly.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.scoring.config import PILLARS, FeatureSpec


# Step 2 ---------------------------------------------------------------------------------


def percentile_risk(value: float, peer_values: list[float], higher_is_safer: bool) -> float:
    """Share of peers at or below this value, flipped for higher-is-safer features."""
    pct = 100 * sum(1 for v in peer_values if v <= value) / len(peer_values)
    return 100 - pct if higher_is_safer else pct


def feature_score(spec: FeatureSpec, value: float | None, peer_values: list[float], min_values: int) -> float | None:
    """0 to 100, higher always riskier. None when the value or a usable peer set is missing.

    Two cases the spec leaves open are handled explicitly:
    - A yes/no feature scores 100 when risky and 0 when not. Ranking a 0/1 value against
      peers who are almost all 0 would otherwise give every company a score of 100.
    - With fewer than `min_values` peer values the percentile carries no meaning (one
      Glassdoor rating would rank itself 100th), so the feature counts as missing.
    """
    if value is None:
        return None
    if spec.binary:
        risky = value >= 1
        return 100.0 if risky != spec.higher_is_safer else 0.0
    if len(peer_values) < min_values:
        return None
    return percentile_risk(value, peer_values, spec.higher_is_safer)


# Step 3 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsEvent:
    key: str
    signal: str
    published_at: datetime
    label: str
    severity: int = 0


@dataclass(frozen=True)
class ScoredEvent:
    event: NewsEvent
    points: float
    decay: float

    @property
    def decayed_points(self) -> float:
        return self.points * self.decay


def news_score(events: list[NewsEvent], as_of: datetime, config: dict[str, Any]) -> tuple[float, list[ScoredEvent]]:
    """Points per event type, halved every `news_half_life_days`, summed and clamped to 0-100.

    One story is counted once: events arrive already grouped by story, and the spec's
    rule is applied on top: the same type within `news_duplicate_window_days` counts once,
    and at most `news_max_events_per_type` events of a type count.
    """
    points_table = config["news_points"]
    half_life = config["news_half_life_days"]
    window = config["news_duplicate_window_days"]
    per_type = config["news_max_events_per_type"]
    multipliers = config.get("severity_multipliers")

    kept: list[ScoredEvent] = []
    kept_dates: dict[str, list[datetime]] = {}
    for event in sorted(events, key=lambda e: e.published_at, reverse=True):
        points = points_table.get(event.signal)
        if not points:
            continue
        dates = kept_dates.setdefault(event.signal, [])
        if len(dates) >= per_type:
            continue
        if any(abs((event.published_at - other).days) < window for other in dates):
            continue
        if multipliers:
            points *= multipliers.get(str(event.severity), 1.0)
        days_old = max((as_of - event.published_at).total_seconds() / 86400, 0)
        kept.append(ScoredEvent(event, float(points), 0.5 ** (days_old / half_life)))
        dates.append(event.published_at)

    total = sum(item.decayed_points for item in kept)
    return min(max(total, 0.0), 100.0), kept


# Steps 4 to 7 ---------------------------------------------------------------------------


def pillar_score(items: list[tuple[float, float | None]]) -> tuple[float | None, float]:
    """Weighted average of the feature scores that exist, and the pillar's coverage."""
    available = [(w, s) for w, s in items if s is not None]
    if not available:
        return None, 0.0
    total_w = sum(w for w, _ in items)
    used_w = sum(w for w, _ in available)
    return sum(w * s for w, s in available) / used_w, used_w / total_w


@dataclass(frozen=True)
class Overall:
    raw: float | None
    final: float | None
    coverage: float
    effective_weights: dict[str, float] = field(default_factory=dict)


def overall_score(
    pillars: dict[str, tuple[float | None, float]],
    base_weights: dict[str, float],
    sector_average: float | None,
    config: dict[str, Any],
) -> Overall:
    """Coverage-weighted pillar average, then pulled toward the sector average when thin.

    No sector average yet (no peers scored) means no pull: the raw score stands.
    """
    eff = {k: base_weights[k] * cov for k, (sc, cov) in pillars.items() if sc is not None and cov > 0}
    coverage = sum(eff.values()) / sum(base_weights.values())
    if coverage < config["min_coverage_to_show_score"]:
        return Overall(None, None, coverage, eff)

    raw = sum(eff[k] * pillars[k][0] for k in eff) / sum(eff.values())
    shrink = config["shrinkage"]
    lam = shrink["base"] + shrink["slope"] * coverage
    anchor = raw if sector_average is None else sector_average
    final = lam * raw + (1 - lam) * anchor
    return Overall(raw, min(max(final, 0.0), 100.0), coverage, eff)


def band_for(score: float, config: dict[str, Any]) -> str:
    bands = config["bands"]
    rounded = round(score)
    if rounded <= bands["low"]:
        return "Low"
    if rounded <= bands["moderate"]:
        return "Moderate"
    if rounded <= bands["elevated"]:
        return "Elevated"
    return "High"


def confidence_for(coverage: float, config: dict[str, Any]) -> str:
    if coverage >= config["confidence"]["high"]:
        return "High"
    if coverage >= config["confidence"]["medium"]:
        return "Medium"
    return "Limited data"


# Step 8 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Driver:
    label: str
    pillar: str
    points: float


def drivers(
    features: list[tuple[FeatureSpec, float, float, str]],
    feature_pillar_coverage_weights: dict[str, float],
    effective_weights: dict[str, float],
    news: tuple[float, float, list[ScoredEvent]] | None,
) -> tuple[float, list[Driver]]:
    """Each feature's contribution against the peer baseline. Returns (baseline, drivers).

    `features` holds (spec, feature score, peer average score, display label) for the
    features that were scored; `feature_pillar_coverage_weights` is each pillar's sum of
    available feature weights. Weights are normalised exactly as the score was computed,
    so baseline + all contributions equals the pre-shrinkage score (test 4).
    """
    total_eff = sum(effective_weights.values())
    baseline = 0.0
    out: list[Driver] = []

    for spec, score, peer_avg, label in features:
        if spec.pillar not in effective_weights:
            continue
        omega = (effective_weights[spec.pillar] / total_eff) * (spec.weight / feature_pillar_coverage_weights[spec.pillar])
        baseline += omega * peer_avg
        out.append(Driver(label, spec.pillar, omega * (score - peer_avg)))

    if news is not None and "news" in effective_weights:
        score, peer_avg, events = news
        omega = effective_weights["news"] / total_eff
        baseline += omega * peer_avg
        contribution = omega * (score - peer_avg)
        total_points = sum(item.decayed_points for item in events)
        if events and abs(total_points) > 1e-9:
            for item in events:
                out.append(Driver(item.event.label, "news", contribution * item.decayed_points / total_points))
        else:
            out.append(Driver("No significant news in the period" if not events else "News overall", "news", contribution))

    return baseline, out


def top_drivers(items: list[Driver], raising: int, lowering: int) -> list[Driver]:
    up = sorted((d for d in items if d.points > 0), key=lambda d: d.points, reverse=True)[:raising]
    down = sorted((d for d in items if d.points < 0), key=lambda d: d.points)[:lowering]
    return up + down


__all__ = [
    "PILLARS",
    "Driver",
    "NewsEvent",
    "Overall",
    "ScoredEvent",
    "band_for",
    "confidence_for",
    "drivers",
    "feature_score",
    "news_score",
    "overall_score",
    "percentile_risk",
    "pillar_score",
    "top_drivers",
]
