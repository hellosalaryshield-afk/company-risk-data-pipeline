import argparse
import time

from app.companies.repository import CompanyRepository
from app.config.observability import configure_observability, step
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.pipeline.news_signal_collection import (
    DISCOVERY_MODES,
    NewsSignalCollectionError,
    collect_news_signals_for_company,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find recent news articles for each company, read their full text, and have Gemini "
            "flag layoff-risk signals. Articles already classified are skipped, so this is safe "
            "to run nightly."
        )
    )
    parser.add_argument("--company", help="Run for one company only, by name or alias")
    parser.add_argument("--timespan", default="1m", help="GDELT search window, for example 1w or 1m")
    parser.add_argument("--max-articles", type=int, default=10, help="Articles to consider per company")
    parser.add_argument("--discovery", choices=DISCOVERY_MODES, default="auto", help="Where to find article links")
    parser.add_argument("--limit", type=int, help="Stop after this many companies")
    parser.add_argument("--start-at", type=int, default=0, help="Skip this many companies, to resume a run")
    parser.add_argument(
        "--retry-pause",
        type=float,
        default=60.0,
        help="Seconds to wait after a failed company before continuing",
    )
    args = parser.parse_args()

    settings = get_settings()
    if configure_observability(settings):
        print("Tracing to Logfire is on.")
    db = next(get_db_session())
    if args.company:
        names = [args.company]
    else:
        companies = CompanyRepository(db).list_companies()[args.start_at :]
        if args.limit:
            companies = companies[: args.limit]
        names = [company.canonical_name for company in companies]

    print(f"Collecting AI news signals for {len(names)} companies with {settings.gemini_model}.\n")
    completed = failed = 0

    for index, name in enumerate(names, start=1):
        try:
            with step("News signals for {company}", company=name):
                result = collect_news_signals_for_company(
                    db=db,
                    query=name,
                    settings=settings,
                    timespan=args.timespan,
                    max_articles=args.max_articles,
                    discovery=args.discovery,
                )
        except NewsSignalCollectionError as exc:
            print(f"Cannot run: {exc}")
            raise SystemExit(1) from exc

        status = result.get("status")
        if status != "completed":
            failed += 1
            print(f"[{index}/{len(names)}] {name:30} FAIL  {result.get('message') or status}")
            if args.retry_pause > 0 and index < len(names):
                print(f"          backing off {args.retry_pause:.0f}s")
                time.sleep(args.retry_pause)
            continue

        completed += 1
        print(
            f"[{index}/{len(names)}] {name:30} ok    "
            f"found {result['discovered']}, new {result['discovered'] - result['already_seen']}, "
            f"readable {result['text_fetched']}, classified {result['classified']}, "
            f"risk articles {result['risk_articles']}, new risk events {result['new_risk_events']}"
        )
        for article in result["articles"]:
            if article["is_risk"]:
                marker = "new event" if article["new_event"] else "same event as before"
                print(f"          ! {article['signal']} (severity {article['severity']}, {marker}): {article['event']}")
        for note in result.get("notes") or []:
            print(f"          note: {note}")
        if result.get("quota_hit"):
            print("\nGemini quota reached. Stopping; rerun later to continue where this left off.")
            break

    print(f"\nDone. completed={completed} failed={failed}")
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
