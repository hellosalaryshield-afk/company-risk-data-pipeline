import argparse
import sys

from sqlalchemy import select

from app.database.session import get_db_session
from app.features.point_in_time import ensure_utc
from app.models.collection import KpiObservation

# Rows written before the news collector was corrected carry published_at = now(), taken from
# the application clock, while collected_at came from the database clock at transaction start.
# That makes published_at land a few seconds after collected_at, which reads as "collected
# before it was published" and is impossible.
#
# The repair is to clear published_at on those rows rather than invent a date for them. We do
# not know when the underlying articles were published, because that was never recorded. NULL
# is the honest answer: the leakage check will then report them as unverifiable, which is
# true, instead of corrupt.


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Clear publication dates on rows where collected_at precedes published_at."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the change. Without this the script only reports what it would do.",
    )
    args = parser.parse_args()

    db = next(get_db_session())
    rows = db.scalars(select(KpiObservation).where(KpiObservation.published_at.is_not(None))).all()

    corrupt = [
        row
        for row in rows
        if ensure_utc(row.collected_at)
        and ensure_utc(row.published_at)
        and ensure_utc(row.collected_at) < ensure_utc(row.published_at)
    ]

    if not corrupt:
        print("No rows with impossible timestamps. Nothing to repair.")
        return

    print(f"Found {len(corrupt)} rows where collected_at precedes published_at.")
    by_kpi: dict[str, int] = {}
    for row in corrupt:
        by_kpi[row.kpi_name] = by_kpi.get(row.kpi_name, 0) + 1
    for kpi_name, count in sorted(by_kpi.items()):
        print(f"  {kpi_name:34} {count}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to clear published_at on these rows.")
        print("The values themselves are kept; only the unreliable publication date is removed.")
        return

    for row in corrupt:
        row.published_at = None
    db.commit()

    print(f"\nCleared published_at on {len(corrupt)} rows.")
    print("Re-run scripts/check_leakage.py to confirm.")
    print("Re-collecting these companies will write correctly dated rows that supersede them.")


if __name__ == "__main__":
    main()
