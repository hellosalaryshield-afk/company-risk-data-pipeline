import argparse

from app.config.settings import get_settings
from app.database.session import get_db_session
from app.pipeline.news_collection import collect_news_for_company


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect NewsAPI records for one company.")
    parser.add_argument("company_name", help="Company name or alias, for example TCS")
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=25)
    args = parser.parse_args()

    db = next(get_db_session())
    result = collect_news_for_company(
        db=db,
        query=args.company_name,
        settings=get_settings(),
        days_back=args.days_back,
        page_size=args.page_size,
    )

    print(result)


if __name__ == "__main__":
    main()
