from datetime import UTC, datetime
from unittest.mock import Mock

from app.pipeline.news_collection import collect_news_for_company
from app.sources.base import SourceArticle, SourceFetchResult


class SettingsStub:
    news_api_key = None


def test_collect_news_for_company_stores_records_and_kpis(db_session):
    company = db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["TCS"])
    fetch_result = SourceFetchResult(
        source_name="newsapi",
        records=[
            SourceArticle(
                title="TCS announces restructuring plan",
                description="The company starts cost rationalization.",
                url="https://example.com/1",
                source_name="Example",
                published_at=datetime(2026, 9, 1, tzinfo=UTC),
                raw_data={"title": "TCS announces restructuring plan"},
            ),
            SourceArticle(
                title="TCS hiring expands in India",
                description="The company opens new roles.",
                url="https://example.com/2",
                source_name="Example",
                published_at=datetime(2026, 9, 1, tzinfo=UTC),
                raw_data={"title": "TCS hiring expands in India"},
            ),
        ],
        metadata={"status": "ok", "total_results": 2},
    )

    result = collect_news_for_company(
        db=db_session.session,
        query="TCS",
        settings=SettingsStub(),
        fetch_result=fetch_result,
    )

    assert result["status"] == "completed"
    assert result["company"]["id"] == company.id
    assert result["records_stored"] == 2
    assert result["kpis"]["news_article_count"] == 2
    assert result["kpis"]["restructuring_news_count"] == 1


def test_collect_news_for_unmatched_company_returns_candidates(db_session):
    db_session.create_company(canonical_name="Tata Consultancy Services", aliases=["TCS"])

    result = collect_news_for_company(
        db=db_session.session,
        query="Tata",
        settings=SettingsStub(),
        fetch_result=Mock(),
    )

    assert result["status"] == "ambiguous"
    assert result["candidates"] == ["Tata Consultancy Services"]
