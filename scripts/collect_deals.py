import argparse
import sys

from app.database.session import get_db_session
from app.pipeline.deals_collection import collect_deals_for_registry


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=(
            "Collect one day of NSE bulk and block deals and attach them to matching companies. "
            "Each NSE file covers a single trading day, so run this daily to build history."
        )
    )
    parser.parse_args()

    db = next(get_db_session())
    result = collect_deals_for_registry(db)

    if result["status"] != "completed":
        print(f"Status: {result['status']}. {result.get('message', '')}")
        raise SystemExit(2)

    print(f"Deals in file       : {result['deals_in_file']}")
    print(f"Symbols in file     : {result['symbols_in_file']}")
    print(f"Companies matched   : {result['companies_matched']}")

    if not result["matches"]:
        print("\nNo registry company traded in large lots today. That is a normal result.")
        return

    print()
    for match in result["matches"]:
        kpis = match["kpis"]
        sell_share = kpis.get("deal_sell_share_pct")
        share_text = f"{sell_share:.1f}% sold" if sell_share is not None else "-"
        print(
            f"  {match['canonical_name']:28} {match['symbol']:12} "
            f"{match['deal_count']:>3} deals  net {kpis.get('deal_net_quantity', 0):>+14,.0f}  {share_text}"
        )


if __name__ == "__main__":
    main()
