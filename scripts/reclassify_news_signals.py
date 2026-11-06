import argparse

from sqlalchemy import delete, inspect, select

from app.companies.repository import CompanyRepository
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.models.collection import KpiObservation, SourceRecord
from app.pipeline.news_signal_collection import (
    DiscoveredArticle,
    collect_news_signals_for_company,
    get_or_create_news_signal_source,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-classify articles already collected, after the news categories change. Article "
            "text is not stored, so each article is fetched again and sent to Gemini again. "
            "Dry run unless --apply is given."
        )
    )
    parser.add_argument("--company", help="One company only, by name or alias")
    parser.add_argument("--apply", action="store_true", help="Replace the stored classifications")
    args = parser.parse_args()

    db = next(get_db_session())
    settings = get_settings()
    source = get_or_create_news_signal_source(db)
    repository = CompanyRepository(db)
    companies = repository.list_companies()
    if args.company:
        companies = [c for c in companies if c.canonical_name.lower() == args.company.lower()]

    for company in companies:
        records = db.scalars(
            select(SourceRecord).where(SourceRecord.company_id == company.id, SourceRecord.source_id == source.id)
        ).all()
        if not records:
            continue
        articles = [
            DiscoveredArticle(
                url=record.url,
                title=record.title or "",
                published_at=record.published_at,
                domain=(record.raw_data or {}).get("domain"),
                discovered_via=(record.raw_data or {}).get("discovered_via") or "stored",
            )
            for record in records
            if record.url
        ]
        print(f"{company.canonical_name:28} {len(articles)} stored articles")
        if not args.apply:
            continue

        # Keep a copy, clear this source's records and KPIs for the company, then run the
        # articles through the current classifier as if newly discovered. If Gemini fails
        # part-way, the copy goes back so nothing is lost.
        kpis = db.scalars(
            select(KpiObservation).where(KpiObservation.company_id == company.id, KpiObservation.source_id == source.id)
        ).all()
        snapshot = [copy_row(row) for row in [*records, *kpis]]
        clear(db, company.id, source.id)
        result = collect_news_signals_for_company(
            db=db, query=company.canonical_name, settings=settings, articles=articles, max_articles=len(articles)
        )
        failures = [note for note in result.get("notes") or [] if "Gemini" in note]
        if failures or result.get("quota_hit"):
            clear(db, company.id, source.id)
            db.add_all([row_class(**values) for row_class, values in snapshot])
            db.commit()
            print(f"{'':28} Gemini failed ({failures[0] if failures else 'quota'}); previous classifications restored.")
            continue
        print(
            f"{'':28} re-classified {result['classified']}, unreadable {result['text_unavailable']}, "
            f"risk {result['risk_articles']}, notes {result.get('notes') or []}"
        )
        for article in result["articles"]:
            marker = "RISK" if article["is_risk"] else "    "
            about = article["event"] if article["about_company"] else "(not about the company)"
            print(f"{'':30}{marker} {article['signal']:18} {about[:70]}")

    if not args.apply:
        print("\nDry run. Add --apply to re-classify.")


def copy_row(row) -> tuple[type, dict]:
    """A detached copy of a row's column values, without its id."""
    mapper = inspect(row).mapper
    values = {attr.key: getattr(row, attr.key) for attr in mapper.column_attrs if attr.key != "id"}
    return type(row), values


def clear(db, company_id: int, source_id: int) -> None:
    db.execute(delete(KpiObservation).where(KpiObservation.company_id == company_id, KpiObservation.source_id == source_id))
    db.execute(delete(SourceRecord).where(SourceRecord.company_id == company_id, SourceRecord.source_id == source_id))
    db.commit()


if __name__ == "__main__":
    main()
