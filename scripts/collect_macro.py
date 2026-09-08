import argparse

from app.database.session import get_db_session
from app.pipeline.macro_collection import MACRO_INDICATORS, collect_macro_indicators


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect macro and index covariates.")
    parser.add_argument("--range", dest="range_period", default="6mo", help="History window, for example 6mo or 1y")
    parser.add_argument("--interval", default="1d", help="Sampling interval, for example 1d or 1wk")
    parser.add_argument(
        "--keys",
        nargs="*",
        help=f"Subset of indicators. Available: {', '.join(item.key for item in MACRO_INDICATORS)}",
    )
    args = parser.parse_args()

    db = next(get_db_session())
    result = collect_macro_indicators(
        db=db,
        range_period=args.range_period,
        interval=args.interval,
        keys=args.keys,
    )

    print(f"Status: {result['status']} (run {result['collection_run_id']})")
    for item in result["indicators_collected"]:
        change = item["change_pct_period"]
        change_text = f"{change:+.2f}%" if change is not None else "n/a"
        print(f"  {item['label']:34} {item['value']:>14,.2f}  {change_text:>9}  [{item['category']}]")

    for failure in result["indicators_failed"]:
        print(f"  FAILED {failure['key']}: {failure['message']}")

    if result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
