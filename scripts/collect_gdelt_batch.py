import argparse
import time

from app.companies.repository import CompanyRepository
from app.database.session import get_db_session
from app.pipeline.gdelt_collection import collect_gdelt_for_company
from app.sources.gdelt import MIN_REQUEST_INTERVAL_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect GDELT news tone for the whole company registry. GDELT is rate limited, "
            "so this is the supported way to refresh it at pilot scale. Run it on a schedule "
            "and let the API read the stored values."
        )
    )
    parser.add_argument("--timespan", default="3m", help="History window, for example 3m or 12m")
    parser.add_argument("--max-articles", type=int, default=25, help="Articles to sample per company")
    parser.add_argument("--limit", type=int, help="Stop after this many companies")
    parser.add_argument("--start-at", type=int, default=0, help="Skip this many companies, to resume a run")
    parser.add_argument(
        "--retry-pause",
        type=float,
        default=60.0,
        help="Seconds to wait after a rate-limit refusal before continuing",
    )
    args = parser.parse_args()

    db = next(get_db_session())
    companies = CompanyRepository(db).list_companies()[args.start_at :]
    if args.limit:
        companies = companies[: args.limit]

    total = len(companies)
    estimate_minutes = total * MIN_REQUEST_INTERVAL_SECONDS * 3 / 60
    print(f"Collecting GDELT for {total} companies. Rough estimate: {estimate_minutes:.0f} minutes.")
    print("GDELT allows about one request every five seconds and each company needs three.\n")

    completed = rate_limited = empty = 0

    for index, company in enumerate(companies, start=1):
        result = collect_gdelt_for_company(
            db=db,
            query=company.canonical_name,
            timespan=args.timespan,
            max_articles=args.max_articles,
        )
        status = result.get("status")

        if status == "completed" and result.get("record_found"):
            completed += 1
            kpis = result.get("kpis") or {}
            tone = kpis.get("gdelt_avg_tone")
            tone_text = f"tone {tone:+.2f}" if tone is not None else "no tone"
            print(f"[{index}/{total}] {company.canonical_name:34} ok    {tone_text}, {len(kpis)} KPIs")
        elif status == "completed":
            empty += 1
            print(f"[{index}/{total}] {company.canonical_name:34} empty no coverage returned")
        else:
            rate_limited += 1
            print(f"[{index}/{total}] {company.canonical_name:34} FAIL  {result.get('message', status)}")
            # Back off hard: continuing at speed after a refusal only earns more refusals.
            if args.retry_pause > 0 and index < total:
                print(f"          backing off {args.retry_pause:.0f}s")
                time.sleep(args.retry_pause)

    print(f"\nDone. completed={completed} empty={empty} failed={rate_limited}")
    if rate_limited:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
