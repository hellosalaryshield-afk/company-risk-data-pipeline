from statistics import fmean

from app.sources.gdelt import GdeltFetchResult, GdeltTimeline

# GDELT tone runs roughly -10 (very negative coverage) to +10 (very positive). That is the
# opposite direction to every other KPI in this project, where a higher number means more
# risk. Raw tone is stored as-is because it is the interpretable figure, and the derived
# risk-shaped signals below are named so the direction is unambiguous:
#
#   gdelt_avg_tone            raw, LOWER is worse
#   gdelt_worst_day_tone      raw, LOWER is worse
#   gdelt_negative_day_pct    derived, HIGHER is worse
#   gdelt_tone_decline        derived, HIGHER is worse (positive = coverage worsening)
#   gdelt_volume_spike_ratio  derived, HIGHER is worse (a burst of coverage)
TONE_KPIS = ("gdelt_avg_tone", "gdelt_worst_day_tone")
RISK_SHAPED_KPIS = ("gdelt_negative_day_pct", "gdelt_tone_decline", "gdelt_volume_spike_ratio")


def split_half_change(values: list[float]) -> float | None:
    """Earlier-half mean minus recent-half mean.

    Positive means the recent half is lower than the earlier half. For tone that means
    coverage got more negative, so positive is the risk direction.
    """
    if len(values) < 4:
        return None

    midpoint = len(values) // 2
    earlier = values[:midpoint]
    recent = values[midpoint:]
    return round(fmean(earlier) - fmean(recent), 4)


def negative_day_pct(values: list[float]) -> float | None:
    if not values:
        return None
    negative = sum(1 for value in values if value < 0)
    return round(negative / len(values) * 100, 2)


def spike_ratio(values: list[float]) -> float | None:
    """Peak over average. A value near 1 is steady coverage; a high value is a news event."""
    if not values:
        return None
    average = fmean(values)
    if average <= 0:
        return None
    return round(max(values) / average, 4)


def timeline_stats(timeline: GdeltTimeline | None) -> dict[str, float]:
    if timeline is None or not timeline.values:
        return {}
    return {
        "mean": round(fmean(timeline.values), 4),
        "min": round(min(timeline.values), 4),
        "max": round(max(timeline.values), 4),
        "points": len(timeline.values),
    }


def extract_gdelt_kpis(result: GdeltFetchResult) -> dict[str, float]:
    """Turn GDELT tone and volume series into KPI values.

    Only signals that were actually returned are included, so a partial fetch never
    fabricates a zero.
    """
    kpis: dict[str, float] = {}

    if result.tone and result.tone.values:
        tone_values = result.tone.values
        kpis["gdelt_avg_tone"] = round(fmean(tone_values), 4)
        kpis["gdelt_worst_day_tone"] = round(min(tone_values), 4)
        kpis["gdelt_tone_days"] = float(len(tone_values))

        negative_pct = negative_day_pct(tone_values)
        if negative_pct is not None:
            kpis["gdelt_negative_day_pct"] = negative_pct

        decline = split_half_change(tone_values)
        if decline is not None:
            kpis["gdelt_tone_decline"] = decline

    if result.volume and result.volume.values:
        volume_values = result.volume.values
        kpis["gdelt_volume_mean_pct"] = round(fmean(volume_values), 6)

        spike = spike_ratio(volume_values)
        if spike is not None:
            kpis["gdelt_volume_spike_ratio"] = spike

    if result.articles:
        kpis["gdelt_article_count"] = float(len(result.articles))

    return kpis
