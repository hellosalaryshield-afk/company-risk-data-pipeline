import argparse
import sys

from app.database.session import get_db_session
from app.pipeline.market_history import backfill_registry


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=(
            "Backfill historical market KPIs for every listed company. Yahoo returns the full "
            "price series on the same free call the pipeline already makes, so this turns a "
            "single current reading into years of dated history the model can backtest on."
        )
    )
    parser.add_argument("--range", dest="range_period", default="max", help="History window, e.g. max, 5y, 2y")
    parser.add_argument("--interval", default="1mo", help="Sampling interval: 1mo, 1wk or 1d")
    parser.add_argument("--limit", type=int, help="Stop after this many companies")
    args = parser.parse_args()

    db = next(get_db_session())
    result = backfill_registry(
        db, range_period=args.range_period, interval=args.interval, limit=args.limit
    )

    print(f"Backfill run {result['collection_run_id']}: {result['companies']} listed companies")
    print(f"Observations written: {result['observations_written']}\n")

    for item in result["results"]:
        if item["status"] == "not_applicable":
            print(f"  {item['company']:28} skipped   {item.get('message', 'no usable ticker')[:50]}")
        elif item["status"] == "source_failed":
            print(f"  {item['company']:28} FAILED    {item.get('message')}")
        else:
            print(
                f"  {item['company']:28} {item['observations_written']:>5} written "
                f"({item.get('observations_skipped', 0)} already had) "
                f"{item.get('earliest', '?')} -> {item.get('latest', '?')}"
            )

    print("\nEach value is computed from a trailing window ending at its own date,")
    print("so a 2022 figure contains no 2026 information. Verify with:")
    print("  python scripts/check_leakage.py --as-of 2024-01-01")


if __name__ == "__main__":
    main()
