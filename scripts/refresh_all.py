import argparse
from datetime import UTC, datetime

from app.companies.repository import CompanyRepository
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.pipeline.company_collection import collect_company_data
from app.pipeline.macro_collection import collect_macro_indicators

# Phase 6 of the brief asks for "a documented refresh process - manual re-runs are fine this
# cycle". This is that process: one command that refreshes macro context and then every
# company in the registry, printing a summary a human can check.


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh macro covariates and every company in the registry."
    )
    parser.add_argument("--limit", type=int, help="Stop after this many companies")
    parser.add_argument("--start-at", type=int, default=0, help="Skip this many companies, to resume")
    parser.add_argument("--days-back", type=int, default=30, help="News lookback window")
    parser.add_argument("--range", dest="range_period", default="6mo", help="Market history window")
    parser.add_argument("--gdelt-timespan", default="3m", help="GDELT history window")
    parser.add_argument(
        "--include-metered",
        action="store_true",
        help="Also run sources billed per call, such as the Apify Glassdoor actor. Costs money.",
    )
    parser.add_argument(
        "--skip-slow",
        action="store_true",
        help="Skip rate-limited sources such as GDELT. Much faster; run collect_gdelt_batch.py separately.",
    )
    parser.add_argument("--skip-macro", action="store_true", help="Do not refresh macro indicators")
    args = parser.parse_args()

    started = datetime.now(UTC)
    db = next(get_db_session())
    settings = get_settings()

    if not args.skip_macro:
        print("Refreshing macro and index covariates...")
        macro = collect_macro_indicators(db, range_period=args.range_period)
        print(f"  {macro['status']}: {len(macro['indicators_collected'])} collected, "
              f"{len(macro['indicators_failed'])} failed\n")

    companies = CompanyRepository(db).list_companies()[args.start_at :]
    if args.limit:
        companies = companies[: args.limit]

    total = len(companies)
    print(f"Refreshing {total} companies.")
    if args.include_metered:
        print("  Metered sources ARE enabled. This run will incur Apify charges.")
    if args.skip_slow:
        print("  Slow sources are skipped. Run scripts/collect_gdelt_batch.py separately.")
    print()

    fully_ok = partial = nothing = 0

    for index, company in enumerate(companies, start=1):
        result = collect_company_data(
            db=db,
            query=company.canonical_name,
            settings=settings,
            days_back=args.days_back,
            range_period=args.range_period,
            include_metered_sources=args.include_metered,
            include_slow_sources=not args.skip_slow,
            gdelt_timespan=args.gdelt_timespan,
        )

        succeeded = result.get("sources_succeeded", [])
        failed = result.get("sources_failed", [])

        if succeeded and not failed:
            fully_ok += 1
            mark = "ok"
        elif succeeded:
            partial += 1
            mark = "partial"
        else:
            nothing += 1
            mark = "NONE"

        segment = (result.get("company") or {}).get("company_segment") or "undetermined"
        print(
            f"[{index}/{total}] {company.canonical_name:30} {mark:8} "
            f"{len(succeeded)} ok / {len(failed)} failed  {len(result.get('kpis') or {})} KPIs  [{segment}]"
        )
        for failure in failed:
            detail = next((s for s in result["sources"] if s["source"] == failure), {})
            print(f"           {failure}: {(detail.get('message') or '')[:80]}")

    elapsed = (datetime.now(UTC) - started).total_seconds() / 60
    print(
        f"\nRefresh finished in {elapsed:.1f} min. "
        f"all-sources-ok={fully_ok} partial={partial} no-source-succeeded={nothing}"
    )
    if nothing:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
