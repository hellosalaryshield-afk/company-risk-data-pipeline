from typing import Any

# Glassdoor five-point ratings. For these a LOWER value means more risk, which is the
# opposite of the market drawdown KPIs, so the direction is recorded explicitly rather
# than left for the reader to guess.
RATING_KPIS = {
    "glassdoor_rating": ("rating", "rating_5"),
    "glassdoor_work_life_balance_rating": ("work_life_balance_rating", "rating_5"),
    "glassdoor_career_opportunities_rating": ("career_opportunities_rating", "rating_5"),
    "glassdoor_compensation_rating": ("compensation_benefits_rating", "rating_5"),
    "glassdoor_culture_values_rating": ("culture_values_rating", "rating_5"),
    "glassdoor_diversity_rating": ("diversity_inclusion_rating", "rating_5"),
    "glassdoor_senior_management_rating": ("senior_management_rating", "rating_5"),
}

# Ratings expressed as a 0-1 share rather than out of five.
SHARE_KPIS = {
    "glassdoor_business_outlook_rating": ("business_outlook_rating", "share_0_1"),
    "glassdoor_ceo_rating": ("ceo_rating", "share_0_1"),
    "glassdoor_recommend_to_friend_rating": ("recommend_to_friend_rating", "share_0_1"),
}

COUNT_KPIS = {
    "glassdoor_review_count": ("review_count", "count"),
    "glassdoor_salary_count": ("salary_count", "count"),
    "glassdoor_job_count": ("job_count", "count"),
}

# Every KPI this module can emit, with the direction that indicates higher layoff risk.
LOWER_IS_RISKIER = frozenset(
    {
        *RATING_KPIS,
        *SHARE_KPIS,
        "glassdoor_job_count",
    }
)


def extract_workplace_kpis(normalized: dict[str, Any]) -> dict[str, float]:
    """Turn one normalized Glassdoor profile into KPI values.

    Missing fields are omitted rather than stored as zero, because a zero rating and an
    absent rating mean very different things.
    """
    kpis: dict[str, float] = {}

    for kpi_name, (field, _unit) in {**RATING_KPIS, **SHARE_KPIS, **COUNT_KPIS}.items():
        value = normalized.get(field)
        if value is None:
            continue
        kpis[kpi_name] = float(value)

    return kpis


def unit_for(kpi_name: str) -> str:
    for mapping in (RATING_KPIS, SHARE_KPIS, COUNT_KPIS):
        if kpi_name in mapping:
            return mapping[kpi_name][1]
    return "count"
