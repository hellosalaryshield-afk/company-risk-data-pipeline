import argparse

from app.database.session import get_db_session
from app.pipeline.market_collection import MarketCollectionError, collect_market_for_company


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Yahoo Finance market data for one listed company.")
    parser.add_argument("company_name", help="Company name or alias, for example TCS")
    parser.add_argument("--range", dest="range_period", default="6mo", help="History window, for example 6mo or 1y")
    parser.add_argument("--interval", default="1d", help="Sampling interval, for example 1d or 1wk")
    args = parser.parse_args()

    db = next(get_db_session())
    try:
        result = collect_market_for_company(
            db=db,
            query=args.company_name,
            range_period=args.range_period,
            interval=args.interval,
        )
    except MarketCollectionError as exc:
        print(f"Market collection failed: {exc}")
        raise SystemExit(1) from exc

    print(result)
    if result.get("status") == "source_failed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
