from app.sources.glassdoor import match_confidence, normalize_glassdoor_record, to_float, to_int
from app.sources.workplace_signals import LOWER_IS_RISKIER, extract_workplace_kpis, unit_for

# Trimmed from a real actor response captured on 2026-09-09.
ZS_RECORD = {
    "companyId": 115506,
    "name": "ZS Associates",
    "companyLink": "https://www.glassdoor.com/Overview/Working-at-ZS-Associates-EI_IE115506.11,24.htm",
    "rating": 3.5,
    "reviewCount": 10611,
    "salaryCount": 19874,
    "jobCount": 232,
    "companySize": "10000+ Employees",
    "companySizeCategory": "GIANT",
    "industry": "Business consulting",
    "website": "http://www.zs.com",
    "companyType": "Company - Private",
    "yearFounded": 1983,
    "headquartersLocation": "Evanston, United States",
    "ceo": "Pratap Khedkar",
    "ceoRating": 0.77,
    "businessOutlookRating": 0.67,
    "careerOpportunitiesRating": 3.8,
    "compensationAndBenefitsRating": 3.9,
    "cultureAndValuesRating": 3.5,
    "diversityAndInclusionRating": 3.9,
    "recommendToFriendRating": 0.63,
    "seniorManagementRating": 3.1,
    "workLifeBalanceRating": 2.2,
    "officeLocations": [{"city": "Chicago, IL", "country": "United States"}],
}


def test_exact_name_match_is_high_confidence():
    confidence, reason = match_confidence("ZS Associates", ZS_RECORD)

    assert confidence == "high"
    assert "matches the query exactly" in reason


def test_country_qualified_result_is_rejected():
    """The live actor returned 'Zepto (Mexico)' for a query of 'Zepto'."""
    confidence, reason = match_confidence("Zepto", {"name": "Zepto (Mexico)"})

    assert confidence == "low"
    assert "mexico" in reason.lower()


def test_completely_different_company_is_rejected():
    confidence, reason = match_confidence("Razorpay", {"name": "Stripe"})

    assert confidence == "low"
    assert "shares no words" in reason


def test_missing_name_is_rejected():
    confidence, reason = match_confidence("Razorpay", {})

    assert confidence == "low"
    assert "missing" in reason


def test_legal_suffix_difference_still_matches():
    confidence, _ = match_confidence("Infosys", {"name": "Infosys Limited"})

    assert confidence == "high"


def test_partial_multi_word_match_is_medium():
    confidence, reason = match_confidence("Tata Consultancy Services", {"name": "Tata Consultancy"})

    assert confidence == "medium"
    assert "partial name match" in reason


def test_normalize_maps_the_camel_case_payload():
    normalized = normalize_glassdoor_record(ZS_RECORD)

    assert normalized["company_id"] == 115506
    assert normalized["job_count"] == 232
    assert normalized["review_count"] == 10611
    assert normalized["work_life_balance_rating"] == 2.2
    assert normalized["business_outlook_rating"] == 0.67
    assert normalized["office_location_count"] == 1


def test_number_coercion_handles_missing_and_bad_values():
    assert to_float(None) is None
    assert to_float("") is None
    assert to_float("not a number") is None
    assert to_float("3.5") == 3.5
    assert to_int("232") == 232
    assert to_int(None) is None


def test_extract_workplace_kpis_from_a_real_record():
    kpis = extract_workplace_kpis(normalize_glassdoor_record(ZS_RECORD))

    assert kpis["glassdoor_rating"] == 3.5
    assert kpis["glassdoor_job_count"] == 232.0
    assert kpis["glassdoor_business_outlook_rating"] == 0.67
    assert kpis["glassdoor_work_life_balance_rating"] == 2.2


def test_missing_ratings_are_omitted_not_zeroed():
    kpis = extract_workplace_kpis({"rating": 4.0, "ceo_rating": None, "job_count": None})

    assert kpis == {"glassdoor_rating": 4.0}
    assert "glassdoor_ceo_rating" not in kpis
    assert "glassdoor_job_count" not in kpis


def test_units_distinguish_five_point_ratings_from_shares():
    assert unit_for("glassdoor_rating") == "rating_5"
    assert unit_for("glassdoor_business_outlook_rating") == "share_0_1"
    assert unit_for("glassdoor_job_count") == "count"


def test_ratings_and_job_count_are_marked_lower_is_riskier():
    assert "glassdoor_rating" in LOWER_IS_RISKIER
    assert "glassdoor_job_count" in LOWER_IS_RISKIER
    assert "glassdoor_business_outlook_rating" in LOWER_IS_RISKIER
    # Review volume says nothing about direction of risk on its own.
    assert "glassdoor_review_count" not in LOWER_IS_RISKIER
