import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.normalization import normalize_company_name
from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.config.observability import note, step
from app.config.settings import Settings, get_settings
from app.features.point_in_time import ensure_utc
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.company import Company
from app.models.source import DataSource
from app.sources.article_text import ArticleTextFetcher
from app.sources.gdelt import GdeltClient, GdeltError, parse_gdelt_date
from app.sources.gemini import GeminiClient, GeminiError, GeminiRateLimitError
from app.sources.news_classifier import (
    RISK_SIGNALS,
    ArticleForClassification,
    NewsSignalClassifier,
    describe_company,
)
from app.sources.newsapi import NewsApiClient

NEWS_SIGNAL_SOURCE_NAME = "gemini_news_signals"
CLASSIFIED_RECORD_TYPE = "news_article_signal"
UNREADABLE_RECORD_TYPE = "news_article_unreadable"

# KPIs summarise every classified article published in the trailing window that ends at the
# newest classified article, so earlier runs keep counting toward later windows.
WINDOW_DAYS = 30

DISCOVERY_MODES = ("auto", "gdelt", "newsapi")

UNITS = {
    "news_ai_articles_assessed": "count",
    "news_ai_risk_article_count": "count",
    "news_ai_layoff_article_count": "count",
    "news_ai_risk_article_share_pct": "percent",
    "news_ai_max_risk_severity": "severity",
}


class NewsSignalCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveredArticle:
    url: str
    title: str
    published_at: datetime | None
    domain: str | None
    discovered_via: str


def get_or_create_news_signal_source(db: Session) -> DataSource:
    source = db.scalars(select(DataSource).where(DataSource.name == NEWS_SIGNAL_SOURCE_NAME)).first()
    if source:
        return source

    source = DataSource(
        name=NEWS_SIGNAL_SOURCE_NAME,
        source_type="api",
        base_url="https://generativelanguage.googleapis.com",
        description=(
            "News articles found through GDELT (or NewsAPI as a fallback), full text fetched "
            "from the publisher, and classified by Gemini for layoff-risk signals about the "
            "company. Only the classification is stored, never the article text."
        ),
        requires_auth=True,
        known_limitations=(
            "Depends on GDELT rate limits for discovery and on the Gemini key's quota. Some "
            "publishers block automated fetching or sit behind paywalls, so those articles are "
            "skipped. Classification is a model judgement from the article text and can be wrong. "
            "Only public news is sent to Gemini; never send personal data through a free-tier key."
        ),
    )
    db.add(source)
    db.flush()
    return source


def headline_mentions(title: str, names: list[str]) -> bool:
    normalized_title = f" {normalize_company_name(title)} "
    return any(f" {name} " in normalized_title for name in names if name)


def discover_articles(
    company_name: str,
    settings: Settings,
    timespan: str = "1m",
    max_articles: int = 10,
    discovery: str = "auto",
    headline_names: list[str] | None = None,
) -> tuple[list[DiscoveredArticle], list[str]]:
    """Find candidate article links, GDELT first and NewsAPI as a fallback.

    GDELT matches a name anywhere in the body, so most of its hits are market roundups that
    mention the company in passing. Only articles whose headline names the company, or one
    of its aliases, are kept. NewsAPI already searches headlines only.

    Returns the articles and human-readable notes on anything that went wrong, so a run
    can explain where its articles came from.
    """
    notes: list[str] = []
    articles: list[DiscoveredArticle] = []
    names = [normalize_company_name(name) for name in (headline_names or [company_name])]

    if discovery in ("auto", "gdelt"):
        try:
            # Ask for more than needed, since the headline filter drops most of them.
            items = GdeltClient().fetch_article_list(company_name, timespan=timespan, max_records=max_articles * 5)
            relevant = [item for item in items if item.get("url") and headline_mentions(item.get("title") or "", names)]
            if items and not relevant:
                notes.append(f"GDELT returned {len(items)} articles, none with the company in the headline.")
            for item in relevant:
                articles.append(
                    DiscoveredArticle(
                        url=item["url"],
                        title=item.get("title") or "",
                        published_at=parse_gdelt_date(item.get("seendate")),
                        domain=item.get("domain"),
                        discovered_via="gdelt",
                    )
                )
        except (GdeltError, httpx.HTTPError) as exc:
            notes.append(f"GDELT discovery failed: {exc}")

    if not articles and discovery in ("auto", "newsapi"):
        if not settings.news_api_key:
            notes.append("NewsAPI fallback skipped: NEWS_API_KEY is not configured.")
        else:
            try:
                result = NewsApiClient(settings.news_api_key).fetch_company_news(
                    company_name, days_back=WINDOW_DAYS, page_size=max_articles
                )
                for record in result.records:
                    if record.url:
                        articles.append(
                            DiscoveredArticle(
                                url=record.url,
                                title=record.title,
                                published_at=record.published_at,
                                domain=record.source_name,
                                discovered_via="newsapi",
                            )
                        )
            except httpx.HTTPError as exc:
                notes.append(f"NewsAPI discovery failed: {exc.__class__.__name__}.")

    unique: dict[str, DiscoveredArticle] = {}
    for article in articles:
        unique.setdefault(article.url, article)
    return list(unique.values())[:max_articles], notes


def collect_news_signals_for_company(
    db: Session,
    query: str,
    settings: Settings | None = None,
    timespan: str = "1m",
    max_articles: int = 10,
    discovery: str = "auto",
    batch_size: int = 5,
    articles: list[DiscoveredArticle] | None = None,
    fetcher: ArticleTextFetcher | None = None,
    classifier: NewsSignalClassifier | None = None,
) -> dict:
    settings = settings or get_settings()

    resolved = CompanyResolver(CompanyRepository(db)).resolve(query)
    if not resolved.match:
        return {
            "status": resolved.status,
            "query": query,
            "confidence": resolved.confidence,
            "message": "Company could not be resolved. Pick a candidate or add it to the registry first.",
            "candidates": [candidate.canonical_name for candidate in resolved.candidates],
            "kpis": {},
        }
    company = resolved.match

    if classifier is None:
        if not settings.gemini_api_key:
            raise NewsSignalCollectionError("GEMINI_API_KEY is not configured.")
        classifier = NewsSignalClassifier(GeminiClient(settings.gemini_api_key, settings.gemini_model))
    fetcher = fetcher or ArticleTextFetcher()

    source = get_or_create_news_signal_source(db)
    run = CollectionRun(
        company_id=company.id,
        status="running",
        extra_metadata={"source": NEWS_SIGNAL_SOURCE_NAME, "query": query, "model": classifier.model},
    )
    db.add(run)
    db.flush()

    notes: list[str] = []
    if articles is None:
        with step("1. Find articles for {company}", company=company.canonical_name):
            articles, notes = discover_articles(
                company.canonical_name,
                settings,
                timespan=timespan,
                max_articles=max_articles,
                discovery=discovery,
                headline_names=[company.canonical_name, *(alias.alias for alias in company.aliases)],
            )
            note("Found {count} articles", count=len(articles), notes=notes)

    seen = seen_urls(db, company.id, source.id)
    new_articles = [article for article in articles if article.url not in seen and len(article.url) <= 1000]

    # Publishers re-headline a story under a new URL, so the same text can arrive several
    # times. Counting each copy would inflate the risk KPIs.
    seen_texts = seen_text_hashes(db, company.id, source.id)
    fetch_statuses: Counter[str] = Counter()
    readable: list[tuple[DiscoveredArticle, str]] = []
    for article in new_articles:
        with step("2. Read article: {title}", title=article.title, url=article.url):
            fetched = fetcher.fetch(article.url)
            note("Read status: {status}, {chars} characters", status=fetched.status, chars=len(fetched.text or ""))
        if fetched.ok and text_hash(fetched.text) in seen_texts:
            fetch_statuses["duplicate_text"] += 1
            store_unreadable(db, company, source, run, article, "duplicate_text")
            continue
        fetch_statuses[fetched.status] += 1
        if fetched.ok:
            seen_texts.add(text_hash(fetched.text))
            readable.append((article, fetched.text))
        else:
            store_unreadable(db, company, source, run, article, fetched.status)

    company_description = describe_company(
        company.canonical_name, company.legal_name, company.sector, company.country
    )
    classified_payloads: list[dict] = []
    quota_hit = False
    for start in range(0, len(readable), batch_size):
        batch = readable[start : start + batch_size]
        inputs = [
            ArticleForClassification(
                index=offset,
                title=article.title,
                published=article.published_at.date().isoformat() if article.published_at else None,
                text=text,
            )
            for offset, (article, text) in enumerate(batch)
        ]
        try:
            with step("3. Gemini judges {count} articles", count=len(inputs), model=classifier.model):
                results = classifier.classify(company_description, inputs)
                for offset, (article, _) in enumerate(batch):
                    if offset in results:
                        verdict = results[offset]
                        note(
                            "Judged: {title} -> {signal} (severity {severity})",
                            title=article.title,
                            signal=verdict.signal if verdict.about_company else "not about the company",
                            severity=verdict.severity,
                            reason=verdict.reason,
                        )
        except GeminiRateLimitError as exc:
            notes.append(str(exc))
            quota_hit = True
            break
        except GeminiError as exc:
            notes.append(f"Gemini batch failed: {exc}")
            continue

        for offset, (article, text) in enumerate(batch):
            classification = results.get(offset)
            if classification is None:
                notes.append(f"Gemini returned no usable result for: {article.title[:80]}")
                continue
            payload = classification.as_dict()
            store_classified(db, company, source, run, article, payload, classifier.model, text)
            classified_payloads.append({"title": article.title, "url": article.url, **payload})

    db.flush()
    with step("4. Compute KPIs over the last {days} days", days=WINDOW_DAYS):
        kpis, window = compute_window_kpis(db, company.id, source.id)
        note("KPIs: {kpis}", kpis=kpis)
    for kpi_name, value in kpis.items():
        db.add(
            KpiObservation(
                company_id=company.id,
                source_id=source.id,
                kpi_name=kpi_name,
                value_numeric=Decimal(str(value)),
                unit=UNITS.get(kpi_name),
                observed_at=window["end"],
                published_at=window["end"],
                period_start=window["start"],
                period_end=window["end"],
                confidence="medium",
                extra_metadata={
                    "source": NEWS_SIGNAL_SOURCE_NAME,
                    "window_days": WINDOW_DAYS,
                    "model": classifier.model,
                },
            )
        )

    discovery_failed = not articles and bool(notes)
    run.status = "failed" if discovery_failed else "completed"
    run.completed_at = datetime.now(UTC)
    run.error_message = "; ".join(notes)[:2000] if discovery_failed else None
    summary = {
        "discovered": len(articles),
        "already_seen": len(articles) - len(new_articles),
        "text_fetched": len(readable),
        "text_unavailable": len(new_articles) - len(readable),
        "fetch_statuses": dict(fetch_statuses),
        "classified": len(classified_payloads),
        "risk_articles": sum(1 for item in classified_payloads if item["is_risk"]),
        "quota_hit": quota_hit,
        "notes": notes,
    }
    run.extra_metadata = {**run.extra_metadata, **summary, "kpi_count": len(kpis)}
    db.commit()

    return {
        "status": "source_failed" if discovery_failed else "completed",
        "company": {"id": company.id, "canonical_name": company.canonical_name},
        "collection_run_id": run.id,
        "source": NEWS_SIGNAL_SOURCE_NAME,
        "model": classifier.model,
        "message": "; ".join(notes) or None,
        **summary,
        "articles": classified_payloads,
        "kpis": kpis,
    }


def seen_urls(db: Session, company_id: int, source_id: int) -> set[str]:
    """URLs already handled, readable or not, so no article is fetched or classified twice."""
    statement = select(SourceRecord.url).where(
        SourceRecord.company_id == company_id,
        SourceRecord.source_id == source_id,
        SourceRecord.url.is_not(None),
    )
    return set(db.scalars(statement).all())


def seen_text_hashes(db: Session, company_id: int, source_id: int) -> set[str]:
    statement = select(SourceRecord.raw_data).where(
        SourceRecord.company_id == company_id,
        SourceRecord.source_id == source_id,
        SourceRecord.record_type == CLASSIFIED_RECORD_TYPE,
    )
    return {raw["text_hash"] for raw in db.scalars(statement).all() if raw and raw.get("text_hash")}


def text_hash(text: str) -> str:
    """Fingerprint of the article body, so a duplicate can be recognised without storing it."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]


def store_unreadable(
    db: Session, company: Company, source: DataSource, run: CollectionRun, article: DiscoveredArticle, status: str
) -> None:
    db.add(
        SourceRecord(
            company_id=company.id,
            source_id=source.id,
            collection_run_id=run.id,
            record_type=UNREADABLE_RECORD_TYPE,
            external_id=url_key(article.url),
            title=article.title[:500] or None,
            url=article.url,
            observed_at=article.published_at,
            published_at=article.published_at,
            confidence="low",
            raw_data={
                "url": article.url,
                "title": article.title,
                "domain": article.domain,
                "discovered_via": article.discovered_via,
                "fetch_status": status,
            },
            normalized_data={"fetch_status": status},
        )
    )


def store_classified(
    db: Session,
    company: Company,
    source: DataSource,
    run: CollectionRun,
    article: DiscoveredArticle,
    classification: dict,
    model: str,
    text: str,
) -> None:
    db.add(
        SourceRecord(
            company_id=company.id,
            source_id=source.id,
            collection_run_id=run.id,
            record_type=CLASSIFIED_RECORD_TYPE,
            external_id=url_key(article.url),
            title=article.title[:500] or None,
            url=article.url,
            observed_at=article.published_at,
            published_at=article.published_at,
            confidence=classification["confidence"],
            raw_data={
                "url": article.url,
                "title": article.title,
                "domain": article.domain,
                "discovered_via": article.discovered_via,
                "text_chars": len(text),
                "text_hash": text_hash(text),
            },
            normalized_data={**classification, "model": model},
        )
    )


def compute_window_kpis(db: Session, company_id: int, source_id: int) -> tuple[dict[str, float], dict]:
    """Summarise classified articles in the trailing window ending at the newest one.

    The window ends at the newest article's publication date rather than at collection
    time, so the KPI only contains what was public on its own date.
    """
    statement = select(SourceRecord.published_at, SourceRecord.normalized_data).where(
        SourceRecord.company_id == company_id,
        SourceRecord.source_id == source_id,
        SourceRecord.record_type == CLASSIFIED_RECORD_TYPE,
        SourceRecord.published_at.is_not(None),
    )
    rows = [(ensure_utc(published), data or {}) for published, data in db.execute(statement).all()]
    if not rows:
        return {}, {}

    end = max(published for published, _ in rows)
    start = end - timedelta(days=WINDOW_DAYS)
    in_window = [data for published, data in rows if start <= published <= end]

    about = [data for data in in_window if data.get("about_company")]
    risk = [data for data in about if data.get("signal") in RISK_SIGNALS and (data.get("severity") or 0) > 0]
    layoffs = [data for data in risk if data.get("signal") == "layoffs"]

    kpis: dict[str, float] = {
        "news_ai_articles_assessed": float(len(about)),
        "news_ai_risk_article_count": float(len(risk)),
        "news_ai_layoff_article_count": float(len(layoffs)),
        "news_ai_max_risk_severity": float(max((data["severity"] for data in risk), default=0)),
    }
    if about:
        kpis["news_ai_risk_article_share_pct"] = round(len(risk) / len(about) * 100, 2)
    return kpis, {"start": start, "end": end}
