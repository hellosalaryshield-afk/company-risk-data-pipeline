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
FINANCIALS_SOURCE = "yahoo_finance_fundamentals"
WORKPLACE_SOURCE = "apify_glassdoor_company_search"
GDELT_SOURCE = "gdelt_doc"
NEWS_SIGNALS_SOURCE = "gemini_news_signals"

INDIA_SEGMENTS = (INDIA_LISTED, INDIA_UNLISTED_FUNDED, INDIA_UNLISTED_NON_FUNDED)
FOREIGN_SEGMENTS = (FOREIGN_LISTED, FOREIGN_UNLISTED_FUNDED, FOREIGN_UNLISTED_NON_FUNDED)

# Sources billed per call. They are skipped unless the caller opts in, so a routine refresh
# of the pilot universe cannot quietly run up a bill. See docs/source-feasibility.md for the
# measured per-company cost of each.
METERED_SOURCES = frozenset({WORKPLACE_SOURCE})

# Free, but slow enough to matter. GDELT throttles to roughly one request every five
# seconds and each company needs three calls, so including it adds around 25 seconds to a
# synchronous request. A public-facing call should leave it out and read the values that
# the scheduled batch collector already stored.
# The Gemini news signals fetch every article page and wait on GDELT, so they belong here too.
SLOW_SOURCES = frozenset({GDELT_SOURCE, NEWS_SIGNALS_SOURCE})

# Which sources are worth calling for each segment. A source is listed only where it can
# actually return company-level data, so the orchestrator does not waste calls or record
# misleading failures. See docs/source-feasibility.md for the evidence behind each row.
SEGMENT_SOURCES: dict[str | None, tuple[str, ...]] = {
    INDIA_LISTED: (NEWS_SOURCE, MCA_SOURCE, MARKET_SOURCE, FINANCIALS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE, NEWS_SIGNALS_SOURCE),
    INDIA_UNLISTED_FUNDED: (NEWS_SOURCE, MCA_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE, NEWS_SIGNALS_SOURCE),
    INDIA_UNLISTED_NON_FUNDED: (NEWS_SOURCE, MCA_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE, NEWS_SIGNALS_SOURCE),
    FOREIGN_LISTED: (NEWS_SOURCE, MARKET_SOURCE, FINANCIALS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE, NEWS_SIGNALS_SOURCE),
    FOREIGN_UNLISTED_FUNDED: (NEWS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE, NEWS_SIGNALS_SOURCE),
    FOREIGN_UNLISTED_NON_FUNDED: (NEWS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE, NEWS_SIGNALS_SOURCE),
    # Segment could not be determined. News and workplace work from the company name alone,
    # so they are still safe to run; the rest need the segment to be known first.
    None: (NEWS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE, NEWS_SIGNALS_SOURCE),
}

SKIP_REASONS = {
    MCA_SOURCE: "MCA/Data.gov covers Indian companies only.",
    MARKET_SOURCE: "Yahoo Finance market data covers listed companies only.",
    FINANCIALS_SOURCE: "Financial results are collected for listed companies only.",
    WORKPLACE_SOURCE: "Glassdoor workplace data applies to all segments.",
    GDELT_SOURCE: "GDELT news tone applies to all segments.",
    NEWS_SIGNALS_SOURCE: "Gemini news signals apply to all segments.",
}

ALL_SOURCES: tuple[str, ...] = (
    NEWS_SOURCE,
    MCA_SOURCE,
    MARKET_SOURCE,
    FINANCIALS_SOURCE,
    WORKPLACE_SOURCE,
    GDELT_SOURCE,
    NEWS_SIGNALS_SOURCE,
)


def sources_for_segment(
    segment: str | None,
    include_metered: bool = True,
    include_slow: bool = True,
) -> tuple[str, ...]:
    selected = SEGMENT_SOURCES.get(segment, SEGMENT_SOURCES[None])
    excluded: set[str] = set()
    if not include_metered:
        excluded |= METERED_SOURCES
    if not include_slow:
        excluded |= SLOW_SOURCES
    if not excluded:
        return selected
    return tuple(name for name in selected if name not in excluded)


def is_metered(source_name: str) -> bool:
    return source_name in METERED_SOURCES


def is_slow(source_name: str) -> bool:
    return source_name in SLOW_SOURCES


def slow_skip_reason(source_name: str) -> str:
    return (
        f"{source_name} is rate limited and slow to call, and was not requested. "
        "Set include_slow_sources to run it live, or read values from the scheduled batch."
    )


def skip_reason(source_name: str, segment: str | None) -> str:
    base = SKIP_REASONS.get(source_name, "Source does not apply to this company.")
    return f"{base} Resolved segment: {segment or 'undetermined'}."


def metered_skip_reason(source_name: str) -> str:
    return (
        f"{source_name} is billed per call and was not requested. "
        "Set include_metered_sources to run it."
    )
