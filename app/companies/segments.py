from dataclasses import dataclass

INDIA_LISTED = "INDIA_LISTED"
INDIA_UNLISTED_FUNDED = "INDIA_UNLISTED_FUNDED"
INDIA_UNLISTED_NON_FUNDED = "INDIA_UNLISTED_NON_FUNDED"
FOREIGN_LISTED = "FOREIGN_LISTED"
FOREIGN_UNLISTED_FUNDED = "FOREIGN_UNLISTED_FUNDED"
FOREIGN_UNLISTED_NON_FUNDED = "FOREIGN_UNLISTED_NON_FUNDED"

COMPANY_SEGMENTS = (
    INDIA_LISTED,
    INDIA_UNLISTED_FUNDED,
    INDIA_UNLISTED_NON_FUNDED,
    FOREIGN_LISTED,
    FOREIGN_UNLISTED_FUNDED,
    FOREIGN_UNLISTED_NON_FUNDED,
)

LISTED_SEGMENTS = (INDIA_LISTED, FOREIGN_LISTED)

FUNDING_FUNDED = "funded"
FUNDING_NOT_FUNDED = "not_funded"
FUNDING_UNKNOWN = "unknown"

FUNDING_STATUSES = (FUNDING_FUNDED, FUNDING_NOT_FUNDED, FUNDING_UNKNOWN)

GEOGRAPHY_INDIA = "india"
GEOGRAPHY_FOREIGN = "foreign"

LISTING_LISTED = "listed"
LISTING_UNLISTED = "unlisted"

INDIA_COUNTRY_NAMES = {"india", "in", "ind", "bharat", "republic of india"}
INDIA_EXCHANGES = {"NSE", "BSE", "NSEI", "BSESN"}

SEGMENT_MATRIX = {
    (GEOGRAPHY_INDIA, LISTING_LISTED): INDIA_LISTED,
    (GEOGRAPHY_FOREIGN, LISTING_LISTED): FOREIGN_LISTED,
    (GEOGRAPHY_INDIA, LISTING_UNLISTED, FUNDING_FUNDED): INDIA_UNLISTED_FUNDED,
    (GEOGRAPHY_INDIA, LISTING_UNLISTED, FUNDING_NOT_FUNDED): INDIA_UNLISTED_NON_FUNDED,
    (GEOGRAPHY_FOREIGN, LISTING_UNLISTED, FUNDING_FUNDED): FOREIGN_UNLISTED_FUNDED,
    (GEOGRAPHY_FOREIGN, LISTING_UNLISTED, FUNDING_NOT_FUNDED): FOREIGN_UNLISTED_NON_FUNDED,
}


@dataclass(frozen=True)
class SegmentClassification:
    segment: str | None
    geography: str
    listing_status: str
    funding_status: str
    confidence: str
    reasons: list[str]


def is_listed(ticker: str | None, exchange: str | None) -> bool:
    return bool((ticker or "").strip()) and bool((exchange or "").strip())


def detect_geography(country: str | None, exchange: str | None, cin: str | None) -> tuple[str, str]:
    normalized_country = (country or "").strip().lower()
    if normalized_country in INDIA_COUNTRY_NAMES:
        return GEOGRAPHY_INDIA, "country field is India"
    if normalized_country:
        return GEOGRAPHY_FOREIGN, f"country field is {country}"

    if (exchange or "").strip().upper() in INDIA_EXCHANGES:
        return GEOGRAPHY_INDIA, f"listed on Indian exchange {exchange}"
    if (cin or "").strip():
        return GEOGRAPHY_INDIA, "company has an Indian CIN"

    return GEOGRAPHY_FOREIGN, "country is unknown; defaulted to foreign"


def normalize_funding_status(funding_status: str | None) -> str:
    normalized = (funding_status or "").strip().lower()
    return normalized if normalized in FUNDING_STATUSES else FUNDING_UNKNOWN


def classify_company(
    country: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
    cin: str | None = None,
    funding_status: str | None = None,
) -> SegmentClassification:
    """Classify a company into one of the six client-defined segments.

    Listed companies are classified from ticker/exchange alone. Unlisted companies
    additionally need a known funding status; when funding is unknown the segment is
    left as None so the gap stays visible instead of being silently guessed.
    """
    reasons: list[str] = []
    geography, geography_reason = detect_geography(country, exchange, cin)
    reasons.append(geography_reason)

    listing_status = LISTING_LISTED if is_listed(ticker, exchange) else LISTING_UNLISTED
    normalized_funding = normalize_funding_status(funding_status)

    if listing_status == LISTING_LISTED:
        reasons.append(f"ticker {ticker} on {exchange}")
        segment = SEGMENT_MATRIX[(geography, LISTING_LISTED)]
        confidence = "low" if "defaulted to foreign" in geography_reason else "high"
        return SegmentClassification(
            segment=segment,
            geography=geography,
            listing_status=listing_status,
            funding_status=normalized_funding,
            confidence=confidence,
            reasons=reasons,
        )

    reasons.append("no ticker/exchange recorded")
    if normalized_funding == FUNDING_UNKNOWN:
        reasons.append("funding status unknown; cannot separate funded from non-funded")
        return SegmentClassification(
            segment=None,
            geography=geography,
            listing_status=listing_status,
            funding_status=FUNDING_UNKNOWN,
            confidence="low",
            reasons=reasons,
        )

    reasons.append(f"funding status recorded as {normalized_funding}")
    segment = SEGMENT_MATRIX[(geography, LISTING_UNLISTED, normalized_funding)]
    confidence = "low" if "defaulted to foreign" in geography_reason else "high"
    return SegmentClassification(
        segment=segment,
        geography=geography,
        listing_status=listing_status,
        funding_status=normalized_funding,
        confidence=confidence,
        reasons=reasons,
    )
