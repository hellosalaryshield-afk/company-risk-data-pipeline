import csv
from pathlib import Path

from app.companies.normalization import normalize_company_name
from app.companies.repository import CompanyRepository
from app.database.session import get_db_session

DATA_FILE = Path("data/pilot_companies.csv")


def split_aliases(value: str) -> list[str]:
    return [alias.strip() for alias in value.split("|") if alias.strip()]


def main() -> None:
    db = next(get_db_session())
    repository = CompanyRepository(db)
    created = 0
    skipped = 0

    with DATA_FILE.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            normalized_name = normalize_company_name(row["canonical_name"])
            if repository.get_by_normalized_alias(normalized_name):
                skipped += 1
                continue

            repository.create_company(
                canonical_name=row["canonical_name"],
                aliases=split_aliases(row["aliases"]),
                legal_name=row["legal_name"] or None,
                country=row["country"] or None,
                sector=row["sector"] or None,
                industry=row["industry"] or None,
                ticker=row["ticker"] or None,
                exchange=row["exchange"] or None,
                website=row["website"] or None,
            )
            created += 1

    print(f"Seed complete. Created: {created}. Skipped existing: {skipped}.")


if __name__ == "__main__":
    main()
