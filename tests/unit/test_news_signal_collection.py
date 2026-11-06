from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.database.session import get_db_session
from app.main import app
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.pipeline import news_signal_collection
from app.pipeline.news_signal_collection import (
    CLASSIFIED_RECORD_TYPE,
    UNREADABLE_RECORD_TYPE,
    DiscoveredArticle,
    NewsSignalCollectionError,
    collect_news_signals_for_company,
    discover_articles,
)
from app.sources.article_text import ArticleText
from app.sources.base import SourceArticle, SourceFetchResult
from app.sources.gdelt import GdeltRateLimitError
from app.sources.gemini import GeminiRateLimitError
from app.sources.news_classifier import ArticleClassification


def article(n, day):
    return DiscoveredArticle(
        url=f"https://news.example/{n}",
        title=f"Article {n}",
        published_at=datetime(2026, 10, day, tzinfo=UTC),
        domain="news.example",
        discovered_via="gdelt",
    )


class FakeFetcher:
    def __init__(self, unreadable=()):
        self.unreadable = set(unreadable)
        self.fetched = []

    def fetch(self, url):
        self.fetched.append(url)
        if url in self.unreadable:
            return ArticleText(url=url, text=None, status="http_error", detail="403")
        return ArticleText(url=url, text=f"Full text of {url}", status="ok")


class FakeClassifier:
    """Stands in for Gemini, keyed by the number in each article's title ('Article 3').

    signals maps that number to (signal, about_company, severity) and optionally a fourth
    item, the event description. Like Gemini, it points an article at a known event with
    the same description, or at an earlier article in the batch reporting the same one.
    """

    model = "fake-model"

    def __init__(self, signals, fail_with=None):
        self.signals = signals
        self.fail_with = fail_with
        self.calls = 0
        self.known_events_seen = []

    def classify(self, company, articles, known_events=None):
        self.calls += 1
        self.known_events_seen.append(list(known_events or []))
        if self.fail_with:
            raise self.fail_with
        known_by_summary = {event.summary: event.label for event in known_events or []}
        batch_events: dict[str, int] = {}
        results = {}
        for item in articles:
            number = int(item.title.split()[-1])
            signal, about, severity, *rest = self.signals[number]
            event = rest[0] if rest else f"event {number}"
            event_ref = known_by_summary.get(event, "new")
            same_as = batch_events.get(event) if event_ref == "new" else None
            batch_events.setdefault(event, item.index)
            results[item.index] = ArticleClassification(
                item.index, about, signal, severity, "high", f"reason {number}", event, event_ref, same_as
            )
        return results


SETTINGS = Settings(gemini_api_key="k", news_api_key=None)


@pytest.fixture
def zepto(db_session):
    return db_session.create_company(
        canonical_name="Zepto", aliases=["Kiranakart"], legal_name="Kiranakart Technologies Private Limited"
    )


def run(db_session, articles, classifier, fetcher=None, **kwargs):
    return collect_news_signals_for_company(
        db=db_session.session,
        query="Zepto",
        settings=SETTINGS,
        articles=articles,
        fetcher=fetcher or FakeFetcher(),
        classifier=classifier,
        **kwargs,
    )


def test_classifies_articles_and_stores_kpis(db_session, zepto):
    classifier = FakeClassifier(
        {1: ("layoffs", True, 3), 2: ("funding_raised", True, 0), 3: ("restructuring", True, 2), 4: ("layoffs", False, 3)}
    )
    articles = [article(1, 10), article(2, 12), article(3, 14), article(4, 15)]

    result = run(db_session, articles, classifier)

    assert result["status"] == "completed"
    assert result["classified"] == 4
    assert result["risk_articles"] == 2
    assert result["kpis"] == {
        "news_ai_articles_assessed": 3.0,
        "news_ai_risk_article_count": 2.0,
        "news_ai_risk_event_count": 2.0,
        "news_ai_layoff_event_count": 1.0,
        "news_ai_max_risk_severity": 3.0,
        "news_ai_risk_article_share_pct": 66.67,
    }
    records = db_session.session.query(SourceRecord).filter_by(record_type=CLASSIFIED_RECORD_TYPE).all()
    assert len(records) == 4
    assert {r.normalized_data["model"] for r in records} == {"fake-model"}


def test_never_stores_the_article_text(db_session, zepto):
    run(db_session, [article(1, 10)], FakeClassifier({1: ("layoffs", True, 2)}))

    record = db_session.session.query(SourceRecord).one()
    stored = str(record.raw_data) + str(record.normalized_data)
    assert "Full text of" not in stored
    assert record.raw_data["text_chars"] > 0


def test_kpis_are_dated_by_the_newest_article_not_the_clock(db_session, zepto):
    run(db_session, [article(1, 10), article(2, 20)], FakeClassifier({1: ("layoffs", True, 2), 2: ("neutral", True, 0)}))

    kpi = db_session.session.query(KpiObservation).filter_by(kpi_name="news_ai_risk_article_count").one()
    assert kpi.published_at.date() == datetime(2026, 10, 20).date()
    assert kpi.period_end == kpi.published_at
    assert (kpi.period_end - kpi.period_start).days == 30


def test_old_articles_fall_out_of_the_window(db_session, zepto):
    old = DiscoveredArticle("https://news.example/9", "Article 9", datetime(2026, 8, 1, tzinfo=UTC), None, "gdelt")
    classifier = FakeClassifier({9: ("layoffs", True, 3), 1: ("neutral", True, 0)})

    result = run(db_session, [old, article(1, 20)], classifier)

    assert result["kpis"]["news_ai_risk_article_count"] == 0.0
    assert result["kpis"]["news_ai_articles_assessed"] == 1.0


def test_a_second_run_skips_articles_already_seen(db_session, zepto):
    classifier = FakeClassifier({1: ("layoffs", True, 2), 2: ("neutral", True, 0), 3: ("layoffs", True, 1)})
    run(db_session, [article(1, 10), article(2, 11)], classifier, fetcher=FakeFetcher(unreadable={"https://news.example/2"}))

    fetcher = FakeFetcher()
    result = run(db_session, [article(1, 10), article(2, 11), article(3, 12)], classifier, fetcher=fetcher)

    assert fetcher.fetched == ["https://news.example/3"]
    assert result["already_seen"] == 2
    assert result["classified"] == 1
    # Both runs' articles count toward the trailing window.
    assert result["kpis"]["news_ai_risk_article_count"] == 2.0


def test_unreadable_articles_are_remembered_but_not_classified(db_session, zepto):
    result = run(
        db_session,
        [article(1, 10), article(2, 11)],
        FakeClassifier({1: ("layoffs", True, 2)}),
        fetcher=FakeFetcher(unreadable={"https://news.example/2"}),
    )

    assert result["text_fetched"] == 1
    assert result["text_unavailable"] == 1
    assert result["fetch_statuses"] == {"ok": 1, "http_error": 1}
    unreadable = db_session.session.query(SourceRecord).filter_by(record_type=UNREADABLE_RECORD_TYPE).one()
    assert unreadable.normalized_data == {"fetch_status": "http_error"}


class SameTextFetcher(FakeFetcher):
    """A publisher serving one story under several re-headlined URLs."""

    def fetch(self, url):
        self.fetched.append(url)
        return ArticleText(url=url, text="Infosys cut its annual revenue growth forecast.", status="ok")


def test_the_same_story_under_different_urls_is_counted_once(db_session, zepto):
    classifier = FakeClassifier({n: ("financial_distress", True, 2) for n in range(1, 5)})

    first = run(db_session, [article(1, 10), article(2, 10), article(3, 10)], classifier, fetcher=SameTextFetcher())
    second = run(db_session, [article(4, 11)], classifier, fetcher=SameTextFetcher())

    assert first["classified"] == 1
    assert first["fetch_statuses"] == {"ok": 1, "duplicate_text": 2}
    assert second["classified"] == 0
    assert second["kpis"]["news_ai_risk_article_count"] == 1.0
    stored = db_session.session.query(SourceRecord).filter_by(record_type=CLASSIFIED_RECORD_TYPE).one()
    assert len(stored.raw_data["text_hash"]) == 64


RIGHTS_ISSUE = "Rs 1,000 crore rights issue"


def test_one_event_reported_by_several_outlets_in_one_batch_counts_once(db_session, zepto):
    classifier = FakeClassifier(
        {
            1: ("financial_distress", True, 1, RIGHTS_ISSUE),
            2: ("financial_distress", True, 1, RIGHTS_ISSUE),
            3: ("leadership_exit", True, 1, "CBO leaves"),
        }
    )

    result = run(db_session, [article(1, 10), article(2, 11), article(3, 12)], classifier)

    assert result["risk_articles"] == 3
    assert result["new_risk_events"] == 2
    assert result["kpis"]["news_ai_risk_article_count"] == 3.0
    assert result["kpis"]["news_ai_risk_event_count"] == 2.0
    first, second = (a for a in result["articles"] if a["event"] == RIGHTS_ISSUE)
    assert first["event_id"] == second["event_id"]
    assert first["new_event"] is True and second["new_event"] is False


def test_a_known_event_is_shown_to_gemini_and_matched_across_batches_and_runs(db_session, zepto):
    classifier = FakeClassifier({n: ("financial_distress", True, 1, RIGHTS_ISSUE) for n in range(1, 5)})

    first = run(db_session, [article(1, 10), article(2, 11), article(3, 12)], classifier, batch_size=1)
    second = run(db_session, [article(4, 13)], classifier)

    # Batch 1 saw no known events; later batches and the next run were shown the rights issue.
    assert classifier.known_events_seen[0] == []
    assert [e.summary for e in classifier.known_events_seen[1]] == [RIGHTS_ISSUE]
    assert classifier.known_events_seen[3][0].label == "E1"
    assert first["new_risk_events"] == 1
    assert second["new_risk_events"] == 0
    assert second["kpis"]["news_ai_risk_article_count"] == 4.0
    assert second["kpis"]["news_ai_risk_event_count"] == 1.0


def test_articles_not_about_the_company_get_no_event(db_session, zepto):
    result = run(db_session, [article(1, 10)], FakeClassifier({1: ("neutral", False, 0)}))

    assert result["articles"][0]["event_id"] is None
    assert result["articles"][0]["new_event"] is False


def test_articles_stored_before_event_tracking_count_as_their_own_events(db_session, zepto):
    from app.pipeline.news_signal_collection import compute_window_kpis, get_or_create_news_signal_source

    source = get_or_create_news_signal_source(db_session.session)
    for n in (1, 2):
        db_session.session.add(
            SourceRecord(
                company_id=zepto.id,
                source_id=source.id,
                record_type=CLASSIFIED_RECORD_TYPE,
                url=f"https://old.example/{n}",
                published_at=datetime(2026, 10, n, tzinfo=UTC),
                raw_data={},
                normalized_data={"about_company": True, "signal": "layoffs", "severity": 2},
            )
        )
    db_session.session.flush()

    kpis, _ = compute_window_kpis(db_session.session, zepto.id, source.id)

    assert kpis["news_ai_risk_event_count"] == 2.0
    assert kpis["news_ai_layoff_event_count"] == 2.0


def test_quota_stops_classification_and_leaves_articles_for_the_next_run(db_session, zepto):
    classifier = FakeClassifier({}, fail_with=GeminiRateLimitError("Quota exceeded"))

    result = run(db_session, [article(n, 10) for n in range(1, 8)], classifier, batch_size=3)

    assert classifier.calls == 1
    assert result["quota_hit"] is True
    assert result["classified"] == 0
    assert db_session.session.query(SourceRecord).count() == 0
    assert result["kpis"] == {}


def test_articles_are_sent_in_batches(db_session, zepto):
    classifier = FakeClassifier({n: ("neutral", True, 0) for n in range(1, 8)})

    run(db_session, [article(n, 10) for n in range(1, 8)], classifier, batch_size=3)

    assert classifier.calls == 3


def test_missing_gemini_key_raises(db_session, zepto):
    with pytest.raises(NewsSignalCollectionError, match="GEMINI_API_KEY"):
        collect_news_signals_for_company(
            db=db_session.session, query="Zepto", settings=Settings(gemini_api_key=None), articles=[]
        )


def test_unknown_company_is_not_collected(db_session, zepto):
    result = collect_news_signals_for_company(
        db=db_session.session, query="Nonexistent Co", settings=SETTINGS, articles=[], classifier=FakeClassifier({})
    )

    assert result["status"] == "unmatched"
    assert db_session.session.query(CollectionRun).count() == 0


def test_discovery_failure_is_recorded(db_session, zepto, monkeypatch):
    def refuse(self, company_name, timespan="1m", max_records=25):
        raise GdeltRateLimitError("GDELT rate limit reached (HTTP 429).")

    monkeypatch.setattr(news_signal_collection.GdeltClient, "fetch_article_list", refuse)

    result = collect_news_signals_for_company(
        db=db_session.session, query="Zepto", settings=SETTINGS, fetcher=FakeFetcher(), classifier=FakeClassifier({})
    )

    assert result["status"] == "source_failed"
    assert "GDELT discovery failed" in result["message"]
    collection_run = db_session.session.query(CollectionRun).one()
    assert collection_run.status == "failed"


def test_discovery_falls_back_to_newsapi(monkeypatch):
    def refuse(self, company_name, timespan="1m", max_records=25):
        raise GdeltRateLimitError("GDELT rate limit reached (HTTP 429).")

    def newsapi(self, company_name, days_back=30, page_size=25):
        record = SourceArticle(
            title="Zepto news",
            description=None,
            url="https://news.example/n1",
            source_name="Example",
            published_at=datetime(2026, 10, 1, tzinfo=UTC),
            raw_data={},
        )
        return SourceFetchResult(source_name="newsapi", records=[record, record], metadata={})

    monkeypatch.setattr(news_signal_collection.GdeltClient, "fetch_article_list", refuse)
    monkeypatch.setattr(news_signal_collection.NewsApiClient, "fetch_company_news", newsapi)

    articles, notes = discover_articles("Zepto", Settings(news_api_key="n"), max_articles=5)

    assert [a.url for a in articles] == ["https://news.example/n1"]
    assert articles[0].discovered_via == "newsapi"
    assert notes and "GDELT discovery failed" in notes[0]


def test_gdelt_articles_are_parsed_and_kept_only_when_the_headline_names_the_company(monkeypatch):
    def gdelt(self, company_name, timespan="1m", max_records=25):
        return [
            {"url": "https://a.example/1", "title": "Zepto to cut 300 jobs", "seendate": "20261020T101500Z"},
            {"url": "https://a.example/2", "title": "Kiranakart files for IPO", "seendate": "20261021T000000Z"},
            {"url": "https://a.example/3", "title": "Market recap: top 10 firms lose Rs 1.5 lakh crore"},
            {"url": "https://a.example/4", "title": "Zeptonow rival expands"},
            {"title": "Zepto story with no url"},
        ]

    monkeypatch.setattr(news_signal_collection.GdeltClient, "fetch_article_list", gdelt)

    articles, notes = discover_articles(
        "Zepto", Settings(), discovery="gdelt", headline_names=["Zepto", "Kiranakart"]
    )

    assert notes == []
    assert [a.url for a in articles] == ["https://a.example/1", "https://a.example/2"]
    assert articles[0].published_at == datetime(2026, 10, 20, 10, 15, tzinfo=UTC)


def test_irrelevant_gdelt_results_fall_back_to_newsapi(monkeypatch):
    def gdelt(self, company_name, timespan="1m", max_records=25):
        return [{"url": "https://a.example/3", "title": "Market recap: top 10 firms lose value"}]

    monkeypatch.setattr(news_signal_collection.GdeltClient, "fetch_article_list", gdelt)

    articles, notes = discover_articles("Zepto", Settings(news_api_key=None))

    assert articles == []
    assert "none with the company in the headline" in notes[0]


def test_endpoint_returns_503_without_a_gemini_key(db_session, zepto, monkeypatch):
    monkeypatch.setattr("app.api.routes.get_settings", lambda: Settings(gemini_api_key=None))
    app.dependency_overrides[get_db_session] = lambda: db_session.session
    try:
        response = TestClient(app).post("/collections/news-signals", json={"company_name": "Zepto"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "GEMINI_API_KEY is not configured."
