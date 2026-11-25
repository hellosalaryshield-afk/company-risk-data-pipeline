"""Load data/company_directory.csv into the company_directory table.

    python scripts/load_company_directory.py            # the database in DATABASE_URL
    python scripts/load_company_directory.py --csv other.csv

Re-running replaces the table but keeps links to companies already onboarded, and links
rows to existing companies by CIN, ticker or name.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.companies.directory_lookup import DIRECTORY_CSV, load_directory  # noqa: E402
from app.database.session import get_db_session  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=DIRECTORY_CSV)
    args = parser.parse_args()
    db = next(get_db_session())
    try:
        print(load_directory(db, args.csv))
    finally:
        db.close()


if __name__ == "__main__":
    main()
