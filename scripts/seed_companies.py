import argparse
import csv
from pathlib import Path

from app.companies.normalization import normalize_company_name
from app.companies.repository import CompanyRepository
from app.database.session import get_db_session

DATA_FILE = Path("data/pilot_companies.csv")


def split_aliases(value: str) -> list[str]:
    return [alias.strip() for alias in value.split("|") if alias.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the pilot company registry from CSV.")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Refresh registry fields and aliases for companies that already exist.",
    )
    args = parser.parse_args()

    db = next(get_db_session())
    repository = CompanyRepository(db)
    created = 0
    skipped = 0
    classified = 0
    updated = 0

    with DATA_FILE.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            normalized_name = normalize_company_name(row["canonical_name"])
            company = repository.get_by_normalized_alias(normalized_name)
            if company:
                if args.update_existing:
                    repository.update_company_profile(
                        company,
                        aliases=split_aliases(row["aliases"]),
                        legal_name=row["legal_name"] or None,
                        country=row["country"] or None,
                        sector=row["sector"] or None,
                        industry=row["industry"] or None,
                        funding_status=row.get("funding_status") or None,
                        ticker=row["ticker"] or None,
                        exchange=row["exchange"] or None,
                        website=row["website"] or None,
                    )
                    updated += 1
                else:
                    skipped += 1
            else:
                company = repository.create_company(
                    canonical_name=row["canonical_name"],
                    aliases=split_aliases(row["aliases"]),
                    legal_name=row["legal_name"] or None,
                    country=row["country"] or None,
                    sector=row["sector"] or None,
                    industry=row["industry"] or None,
                    funding_status=row.get("funding_status") or None,
                    ticker=row["ticker"] or None,
                    exchange=row["exchange"] or None,
                    website=row["website"] or None,
                )
                created += 1

            if repository.classify_segment(company).segment:
                classified += 1

    db.commit()
    print(
        f"Seed complete. Created: {created}. Updated: {updated}. "
        f"Skipped existing: {skipped}. Segments assigned: {classified}."
    )


if __name__ == "__main__":
    main()
