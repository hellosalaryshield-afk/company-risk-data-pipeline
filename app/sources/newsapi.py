from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.sources.base import SourceArticle, SourceFetchResult

NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"


class NewsApiClient:
    def __init__(self, api_key: str, timeout_seconds: float = 12.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_company_news(self, company_name: str, days_back: int = 30, page_size: int = 25) -> SourceFetchResult:
        from_date = (datetime.now(UTC) - timedelta(days=days_back)).date().isoformat()
        params = {
            "qInTitle": company_name,
            "from": from_date,
            "sortBy": "relevance",
            "language": "en",
            "pageSize": page_size,
            "apiKey": self.api_key,
        }

        response = httpx.get(NEWSAPI_ENDPOINT, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        articles = [parse_article(article) for article in payload.get("articles", [])]

        return SourceFetchResult(
            source_name="newsapi",
            records=articles,
            metadata={
                "query": company_name,
                "days_back": days_back,
                "total_results": payload.get("totalResults"),
                "status": payload.get("status"),
            },
        )


def parse_article(article: dict[str, Any]) -> SourceArticle:
    published_at = parse_datetime(article.get("publishedAt"))
    source = article.get("source") or {}
    return SourceArticle(
        title=article.get("title") or "",
        description=article.get("description"),
        url=article.get("url"),
        source_name=source.get("name"),
        published_at=published_at,
        raw_data=article,
    )


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)
