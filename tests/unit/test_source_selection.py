from app.companies.segments import (
    FOREIGN_LISTED,
    FOREIGN_UNLISTED_NON_FUNDED,
    INDIA_LISTED,
    INDIA_UNLISTED_FUNDED,
)
from app.pipeline.source_selection import (
    MARKET_SOURCE,
    MCA_SOURCE,
    NEWS_SOURCE,
    skip_reason,
    sources_for_segment,
)


def test_india_listed_gets_all_three_sources():
    assert set(sources_for_segment(INDIA_LISTED)) == {NEWS_SOURCE, MCA_SOURCE, MARKET_SOURCE}


def test_india_unlisted_skips_market_data():
    sources = sources_for_segment(INDIA_UNLISTED_FUNDED)

    assert MARKET_SOURCE not in sources
    assert MCA_SOURCE in sources


def test_foreign_listed_skips_mca():
    sources = sources_for_segment(FOREIGN_LISTED)

    assert MCA_SOURCE not in sources
    assert MARKET_SOURCE in sources


def test_foreign_unlisted_only_gets_news():
    assert sources_for_segment(FOREIGN_UNLISTED_NON_FUNDED) == (NEWS_SOURCE,)


def test_undetermined_segment_falls_back_to_news_only():
    assert sources_for_segment(None) == (NEWS_SOURCE,)


def test_unknown_segment_falls_back_to_news_only():
    assert sources_for_segment("SOMETHING_ELSE") == (NEWS_SOURCE,)


def test_skip_reason_names_the_segment():
    reason = skip_reason(MARKET_SOURCE, INDIA_UNLISTED_FUNDED)

    assert "listed companies only" in reason
    assert INDIA_UNLISTED_FUNDED in reason


def test_skip_reason_handles_undetermined_segment():
    assert "undetermined" in skip_reason(MCA_SOURCE, None)
