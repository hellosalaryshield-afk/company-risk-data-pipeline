import argparse

from app.config.settings import get_settings
from app.database.session import get_db_session
from app.pipeline.workplace_collection import WorkplaceCollectionError, collect_workplace_for_company
from app.sources.glassdoor import COST_PER_RUN_USD


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Collect Glassdoor workplace data for one company. Billed about ${COST_PER_RUN_USD:.2f} per run."
    )
    parser.add_argument("company_name", help="Company name or alias, for example TCS")
    parser.add_argument(
        "--domain",
        default="www.glassdoor.co.in",
        help="Glassdoor domain. Use www.glassdoor.com for non-India companies.",
    )
    args = parser.parse_args()

    db = next(get_db_session())
    try:
        result = collect_workplace_for_company(
            db=db,
            query=args.company_name,
            settings=get_settings(),
            domain=args.domain,
        )
    except WorkplaceCollectionError as exc:
        print(f"Workplace collection failed: {exc}")
        raise SystemExit(1) from exc

    print(f"Status: {result['status']}")
    if result.get("message"):
        print(f"Match:  {result['message']}")

    record = result.get("workplace_record") or {}
    if record:
        print(f"Profile: {record.get('name')} | size {record.get('company_size')} | jobs {record.get('job_count')}")

    for name, value in sorted((result.get("kpis") or {}).items()):
        print(f"  {name:44} {value}")

    if result["status"] == "match_rejected":
        print("No KPIs stored: the returned profile did not match this company.")
        raise SystemExit(3)
    if result["status"] == "source_failed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
