import argparse

from app.database.session import get_db_session
from app.scoring.config import load_config
from app.scoring.service import ALL_GROUP, NEWS_KEY, OVERALL_KEY, build_peer_tables


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Monthly job: rebuild the peer tables the risk score compares each company against "
            "(peer values per feature, peer averages, sector average scores)."
        )
    )
    parser.parse_args()

    config = load_config()
    db = next(get_db_session())
    tables = build_peer_tables(db, config=config)

    print(f"Peer tables rebuilt for weights version {config['weights_version']}.")
    groups = sorted({group for group, _ in tables.stats})
    for group in groups:
        features = sum(1 for g, key in tables.stats if g == group and not key.startswith("__"))
        overall = tables.stats.get((group, OVERALL_KEY))
        news = tables.stats.get((group, NEWS_KEY))
        scored = overall.company_count if overall else 0
        average = f"{overall.average_score:5.1f}" if overall and overall.average_score is not None else "    -"
        news_average = f"{news.average_score:5.1f}" if news and news.average_score is not None else "    -"
        print(f"  {group:42} features {features:2}  scored {scored:3}  avg score {average}  avg news {news_average}")

    narrow = [
        group
        for group in groups
        if group != ALL_GROUP and (tables.stats.get((group, OVERALL_KEY)) is None
                                   or tables.stats[(group, OVERALL_KEY)].company_count < tables.min_group)
    ]
    if narrow:
        print(f"\n{len(narrow)} sector groups have fewer than {tables.min_group} companies, so they use all companies.")


if __name__ == "__main__":
    main()
