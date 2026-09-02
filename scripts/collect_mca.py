import argparse

from app.config.settings import get_settings
from app.database.session import get_db_session
from app.pipeline.mca_collection import McaCollectionError, collect_mca_for_company


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect MCA/Data.gov company master data for one company.")
    parser.add_argument("company_name", help="Company name or alias, for example Razorpay")
    args = parser.parse_args()

    db = next(get_db_session())
    try:
        result = collect_mca_for_company(
            db=db,
            query=args.company_name,
            settings=get_settings(),
        )
    except McaCollectionError as exc:
        print(f"MCA collection failed: {exc}")
        raise SystemExit(1) from exc

    print(result)
    if result.get("status") == "source_failed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
