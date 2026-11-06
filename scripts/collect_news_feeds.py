import argparse

from app.database.session import get_db_session
from app.pipeline.news_feed_collection import collect_news_feeds


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch publisher RSS feeds and store new articles. Feeds only hold their latest items "
            "(roughly a day or two), so run this several times a day."
        )
    )
    parser.parse_args()

    db = next(get_db_session())
    result = collect_news_feeds(db)
    for name, outcome in result["feeds"].items():
        if outcome["status"] == "ok":
            print(f"  {name:30} {outcome['items']:3} items, {outcome['new']:3} new")
        else:
            print(f"  {name:30} FAILED  {outcome['error']}")
    print(f"\nDone. {result['new_items']} new articles stored, {result['failed_feeds']} feeds failed.")


if __name__ == "__main__":
    main()
