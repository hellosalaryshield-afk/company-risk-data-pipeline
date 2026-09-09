from app.companies.segments import (
    FOREIGN_LISTED,
    FOREIGN_UNLISTED_NON_FUNDED,
    INDIA_LISTED,
    INDIA_UNLISTED_FUNDED,
)
from app.pipeline.source_selection import (
    GDELT_SOURCE,
    MARKET_SOURCE,
    MCA_SOURCE,
    NEWS_SOURCE,
    WORKPLACE_SOURCE,
    is_metered,
    is_slow,
    metered_skip_reason,
    skip_reason,
    slow_skip_reason,
    sources_for_segment,
)


def test_india_listed_gets_every_source():
    assert set(sources_for_segment(INDIA_LISTED)) == {
        NEWS_SOURCE,
        MCA_SOURCE,
        MARKET_SOURCE,
        WORKPLACE_SOURCE,
        GDELT_SOURCE,
    }


def test_india_unlisted_skips_market_data():
    sources = sources_for_segment(INDIA_UNLISTED_FUNDED)

    assert MARKET_SOURCE not in sources
    assert MCA_SOURCE in sources


def test_foreign_listed_skips_mca():
    sources = sources_for_segment(FOREIGN_LISTED)

    assert MCA_SOURCE not in sources
    assert MARKET_SOURCE in sources


def test_foreign_unlisted_gets_news_and_workplace():
    assert set(sources_for_segment(FOREIGN_UNLISTED_NON_FUNDED)) == {NEWS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE}


def test_undetermined_segment_falls_back_to_name_only_sources():
    assert set(sources_for_segment(None)) == {NEWS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE}


def test_unknown_segment_falls_back_to_name_only_sources():
    assert set(sources_for_segment("SOMETHING_ELSE")) == {NEWS_SOURCE, WORKPLACE_SOURCE, GDELT_SOURCE}


def test_metered_sources_are_excluded_when_not_requested():
    without = sources_for_segment(INDIA_LISTED, include_metered=False)

    assert WORKPLACE_SOURCE not in without
    assert set(without) == {NEWS_SOURCE, MCA_SOURCE, MARKET_SOURCE, GDELT_SOURCE}


def test_only_the_glassdoor_actor_is_metered():
    assert is_metered(WORKPLACE_SOURCE) is True
    assert is_metered(NEWS_SOURCE) is False
    assert is_metered(MARKET_SOURCE) is False


def test_metered_skip_reason_explains_the_opt_in():
    reason = metered_skip_reason(WORKPLACE_SOURCE)

    assert "billed per call" in reason
    assert "include_metered_sources" in reason


def test_skip_reason_names_the_segment():
    reason = skip_reason(MARKET_SOURCE, INDIA_UNLISTED_FUNDED)

    assert "listed companies only" in reason
    assert INDIA_UNLISTED_FUNDED in reason


def test_skip_reason_handles_undetermined_segment():
    assert "undetermined" in skip_reason(MCA_SOURCE, None)


def test_slow_sources_are_excluded_when_not_requested():
    without = sources_for_segment(INDIA_LISTED, include_slow=False)

    assert GDELT_SOURCE not in without
    assert NEWS_SOURCE in without


def test_only_gdelt_is_slow():
    assert is_slow(GDELT_SOURCE) is True
    assert is_slow(NEWS_SOURCE) is False
    assert is_slow(MARKET_SOURCE) is False


def test_free_and_fast_selection_drops_both_metered_and_slow():
    lean = sources_for_segment(INDIA_LISTED, include_metered=False, include_slow=False)

    assert set(lean) == {NEWS_SOURCE, MCA_SOURCE, MARKET_SOURCE}


def test_slow_skip_reason_explains_the_batch_alternative():
    reason = slow_skip_reason(GDELT_SOURCE)

    assert "rate limited" in reason
    assert "batch" in reason
