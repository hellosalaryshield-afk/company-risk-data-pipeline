import argparse

from app.config.settings import get_settings
from app.database.session import get_db_session
from app.pipeline.company_collection import collect_company_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run every applicable source for one company.")
    parser.add_argument("company_name", help="Company name or alias, for example TCS")
    parser.add_argument("--days-back", type=int, default=30, help="News lookback window in days")
    parser.add_argument("--page-size", type=int, default=25, help="Maximum news articles to fetch")
    parser.add_argument("--range", dest="range_period", default="6mo", help="Market history window")
    args = parser.parse_args()

    db = next(get_db_session())
    result = collect_company_data(
        db=db,
        query=args.company_name,
        settings=get_settings(),
        days_back=args.days_back,
        page_size=args.page_size,
        range_period=args.range_period,
    )

    if not result.get("company"):
        print(f"Status: {result['status']}")
        print(result.get("message", ""))
        for candidate in result.get("candidates", []):
            print(f"  candidate: {candidate['canonical_name']} (id={candidate['id']})")
        raise SystemExit(1)

    company = result["company"]
    print(f"{company['canonical_name']} (id={company['id']}) - segment {company['company_segment'] or 'undetermined'}")
    print(f"Status: {result['status']}")
    for source in result["sources"]:
        message = f" - {source['message']}" if source.get("message") else ""
        print(f"  {source['source']:32} {source['status']}{message}")

    print(f"Collected {len(result['kpis'])} KPI values:")
    for name, value in sorted(result["kpis"].items()):
        print(f"  {name:40} {value}")


if __name__ == "__main__":
    main()
