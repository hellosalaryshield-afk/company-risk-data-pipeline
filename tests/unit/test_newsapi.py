from datetime import UTC, datetime

from app.sources.newsapi import parse_article


def test_parse_article_maps_newsapi_payload() -> None:
    article = parse_article(
        {
            "source": {"name": "Example News"},
            "title": "TCS announces expansion",
            "description": "Hiring to continue.",
            "url": "https://example.com/tcs",
            "publishedAt": "2026-09-01T10:00:00Z",
        }
    )

    assert article.title == "TCS announces expansion"
    assert article.source_name == "Example News"
    assert article.published_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
