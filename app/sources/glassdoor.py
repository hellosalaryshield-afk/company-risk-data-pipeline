from typing import Any

import httpx

from app.companies.normalization import normalize_company_name

# The Apify REST API is called directly rather than through the `apify_client` package.
# One less dependency, and the sync endpoint is a single POST.
APIFY_RUN_SYNC_ENDPOINT = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
GLASSDOOR_ACTOR = "burbn~glassdoor-company-search"
DEFAULT_DOMAIN = "www.glassdoor.co.in"

# Each actor run is billed per event. Kept here so the cost of a refresh is visible in code
# and not only in the Apify dashboard. Verified against a real run on 2026-09-09.
COST_PER_RUN_USD = 0.10


class GlassdoorClient:
    def __init__(
        self,
        api_token: str,
        domain: str = DEFAULT_DOMAIN,
        timeout_seconds: float = 180.0,
    ):
        self.api_token = api_token
        self.domain = domain
        self.timeout_seconds = timeout_seconds

    def fetch_company_profile(self, company_name: str) -> dict[str, Any] | None:
        response = httpx.post(
            APIFY_RUN_SYNC_ENDPOINT.format(actor=GLASSDOOR_ACTOR),
            params={"token": self.api_token, "timeout": int(self.timeout_seconds)},
            json={"query": company_name, "limit": 1, "domain": self.domain},
            timeout=self.timeout_seconds + 20,
        )
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list) or not items:
            return None
        return items[0]


def match_confidence(query: str, record: dict[str, Any]) -> tuple[str, str]:
    """Judge whether the returned profile is actually the company we asked for.

    The actor returns its top search hit with no verification, so a query for the Indian
    "Zepto" can come back as "Zepto (Mexico)". Attaching those ratings to the wrong company
    would silently corrupt the dataset, so every match is scored and low matches are not
    written onto the company record.
    """
    returned_name = record.get("name") or ""
    normalized_query = normalize_company_name(query)
    normalized_result = normalize_company_name(returned_name)

    if not normalized_query or not normalized_result:
        return "low", "Company name missing from the Glassdoor result."

    if normalized_query == normalized_result:
        return "high", "Returned name matches the query exactly."

    query_tokens = set(normalized_query.split())
    result_tokens = set(normalized_result.split())
    overlap = query_tokens & result_tokens

    if not overlap:
        return "low", f"Returned '{returned_name}', which shares no words with the query."

    # A result that adds words, such as a country qualifier, is a likely different entity.
    extra_tokens = result_tokens - query_tokens
    if overlap == query_tokens and extra_tokens:
        return (
            "low",
            f"Returned '{returned_name}', which adds '{' '.join(sorted(extra_tokens))}' "
            "and is probably a different entity.",
        )

    # A shortened name ("Tata Consultancy" for "Tata Consultancy Services") is usually the
    # same company, so it is judged more leniently than one carrying unfamiliar extra words.
    threshold = 0.75 if extra_tokens else 0.6
    coverage = len(overlap) / len(query_tokens)
    if coverage >= threshold:
        return "medium", f"Returned '{returned_name}', a partial name match."

    return "low", f"Returned '{returned_name}', which is a weak name match."


def normalize_glassdoor_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": record.get("companyId"),
        "name": record.get("name"),
        "company_link": record.get("companyLink"),
        "rating": to_float(record.get("rating")),
        "review_count": to_int(record.get("reviewCount")),
        "salary_count": to_int(record.get("salaryCount")),
        "job_count": to_int(record.get("jobCount")),
        "company_size": record.get("companySize"),
        "company_size_category": record.get("companySizeCategory"),
        "industry": record.get("industry"),
        "website": record.get("website"),
        "company_type": record.get("companyType"),
        "year_founded": to_int(record.get("yearFounded")),
        "headquarters_location": record.get("headquartersLocation"),
        "ceo": record.get("ceo"),
        "ceo_rating": to_float(record.get("ceoRating")),
        "business_outlook_rating": to_float(record.get("businessOutlookRating")),
        "career_opportunities_rating": to_float(record.get("careerOpportunitiesRating")),
        "compensation_benefits_rating": to_float(record.get("compensationAndBenefitsRating")),
        "culture_values_rating": to_float(record.get("cultureAndValuesRating")),
        "diversity_inclusion_rating": to_float(record.get("diversityAndInclusionRating")),
        "recommend_to_friend_rating": to_float(record.get("recommendToFriendRating")),
        "senior_management_rating": to_float(record.get("seniorManagementRating")),
        "work_life_balance_rating": to_float(record.get("workLifeBalanceRating")),
        "office_location_count": len(record.get("officeLocations") or []),
    }


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None
