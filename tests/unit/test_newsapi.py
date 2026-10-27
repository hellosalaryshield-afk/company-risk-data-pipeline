from datetime import UTC, datetime

import httpx

from app.sources import newsapi
from app.sources.newsapi import NewsApiClient, parse_article


def test_api_key_is_sent_in_a_header_not_the_url(monkeypatch) -> None:
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append({"params": params, "headers": headers})
        return httpx.Response(200, json={"status": "ok", "articles": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(newsapi.httpx, "get", get)

    NewsApiClient("secret-key").fetch_company_news("TCS")

    assert calls[0]["headers"] == {"X-Api-Key": "secret-key"}
    assert "secret-key" not in str(calls[0]["params"])


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
