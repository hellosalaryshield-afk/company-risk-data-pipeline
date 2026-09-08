from app.companies.segments import (
    FOREIGN_LISTED,
    FOREIGN_UNLISTED_FUNDED,
    FOREIGN_UNLISTED_NON_FUNDED,
    INDIA_LISTED,
    INDIA_UNLISTED_FUNDED,
    INDIA_UNLISTED_NON_FUNDED,
)

NEWS_SOURCE = "newsapi"
MCA_SOURCE = "data_gov_mca_company_master"
MARKET_SOURCE = "yahoo_finance_chart"

INDIA_SEGMENTS = (INDIA_LISTED, INDIA_UNLISTED_FUNDED, INDIA_UNLISTED_NON_FUNDED)
FOREIGN_SEGMENTS = (FOREIGN_LISTED, FOREIGN_UNLISTED_FUNDED, FOREIGN_UNLISTED_NON_FUNDED)

# Which sources are worth calling for each segment. A source is listed only where it can
# actually return company-level data, so the orchestrator does not waste calls or record
# misleading failures. See docs/source-feasibility.md for the evidence behind each row.
SEGMENT_SOURCES: dict[str | None, tuple[str, ...]] = {
    INDIA_LISTED: (NEWS_SOURCE, MCA_SOURCE, MARKET_SOURCE),
    INDIA_UNLISTED_FUNDED: (NEWS_SOURCE, MCA_SOURCE),
    INDIA_UNLISTED_NON_FUNDED: (NEWS_SOURCE, MCA_SOURCE),
    FOREIGN_LISTED: (NEWS_SOURCE, MARKET_SOURCE),
    FOREIGN_UNLISTED_FUNDED: (NEWS_SOURCE,),
    FOREIGN_UNLISTED_NON_FUNDED: (NEWS_SOURCE,),
    # Segment could not be determined. News works from the company name alone, so it is
    # still safe to run; the rest need the segment to be known first.
    None: (NEWS_SOURCE,),
}

SKIP_REASONS = {
    MCA_SOURCE: "MCA/Data.gov covers Indian companies only.",
    MARKET_SOURCE: "Yahoo Finance market data covers listed companies only.",
}


def sources_for_segment(segment: str | None) -> tuple[str, ...]:
    return SEGMENT_SOURCES.get(segment, SEGMENT_SOURCES[None])


def skip_reason(source_name: str, segment: str | None) -> str:
    base = SKIP_REASONS.get(source_name, "Source does not apply to this company.")
    return f"{base} Resolved segment: {segment or 'undetermined'}."
