import argparse
import csv
import time
from pathlib import Path

from app.companies.normalization import normalize_company_name
from app.database.session import create_database_engine
from app.sources.data_gov_mca import parse_mca_date

DEFAULT_IN_DIR = Path("data/raw/mca")
TRIGRAM_INDEX = "ix_mca_companies_normalized_name_trgm"

COLUMNS = (
    "cin",
    "company_name",
    "normalized_name",
    "company_status",
    "company_class",
    "company_category",
    "registered_state",
    "registration_date",
    "listing_status",
    "industrial_classification",
)

# Column lengths in mca_companies, so an unusually long value is trimmed rather than
# failing the whole load.
LIMITS = {
    "company_name": 255,
    "normalized_name": 255,
    "company_status": 40,
    "company_class": 40,
    "company_category": 80,
    "registered_state": 60,
    "listing_status": 20,
    "industrial_classification": 160,
}


def is_closed(status: str | None) -> bool:
    """Companies that no longer exist under this name: struck off, dissolved, merged
    or converted. About 27% of the registry.

    Statuses that signal trouble but not closure, such as Under Liquidation, Under CIRP
    (insolvency) or Under process of striking off, are kept: they are the strongest
    warnings a job seeker can get. Unrecognised statuses are kept too, so a status the
    registry adds later is never dropped silently.
    """
    status = (status or "").strip()
    return (
        status == "Strike Off"
        or status.startswith("Strike Off-")
        or status == "Amalgamated"
        or status.startswith("Converted")
        or "Dissolved" in status
    )


def clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value in ("", "NA") else value


def to_row(record: dict[str, str]) -> tuple | None:
    cin = clean(record.get("CIN"))
    name = clean(record.get("CompanyName"))
    if not cin or not name or len(cin) > 21:
        return None

    values = {
        "cin": cin.upper(),
        "company_name": name,
        "normalized_name": normalize_company_name(name),
        "company_status": clean(record.get("CompanyStatus")),
        "company_class": clean(record.get("CompanyClass")),
        "company_category": clean(record.get("CompanyCategory")),
        "registered_state": clean(record.get("CompanyStateCode")),
        "registration_date": parse_mca_date(clean(record.get("CompanyRegistrationdate_date"))),
        "listing_status": clean(record.get("Listingstatus")),
        "industrial_classification": clean(record.get("CompanyIndustrialClassification")),
    }
    for column, limit in LIMITS.items():
        if values[column]:
            values[column] = values[column][:limit]
    return tuple(values[column] for column in COLUMNS)


def read_pages(paths: list[Path], include_closed: bool, skipped: dict):
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            for record in csv.DictReader(handle):
                if not include_closed and is_closed(record.get("CompanyStatus")):
                    skipped["closed"] += 1
                    continue
                row = to_row(record)
                if row:
                    yield row
                else:
                    skipped["invalid"] += 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Load downloaded MCA CSV pages into mca_companies. Re-running updates existing "
            "rows by CIN, so it is safe to run again after a fresh download."
        )
    )
    parser.add_argument("--in-dir", type=Path, default=DEFAULT_IN_DIR)
    parser.add_argument(
        "--include-closed", action="store_true", help="Also load struck-off, dissolved and merged companies"
    )
    parser.add_argument(
        "--replace", action="store_true", help="Empty the table first, so rows dropped from the source go too"
    )
    args = parser.parse_args()

    paths = sorted(args.in_dir.glob("page_*.csv"))
    if not paths:
        raise SystemExit(f"No pages found in {args.in_dir}. Run scripts/download_mca_bulk.py first.")
    scope = "all companies" if args.include_closed else "skipping closed companies"
    print(f"Loading {len(paths):,} pages, {scope}{', replacing existing rows' if args.replace else ''}...")
    skipped = {"closed": 0, "invalid": 0}

    engine = create_database_engine()
    connection = engine.raw_connection()
    started = time.monotonic()
    try:
        cursor = connection.cursor()
        if args.replace:
            cursor.execute("TRUNCATE mca_companies")
        # Building the trigram index once at the end is far faster than updating it
        # for every one of millions of inserted rows.
        cursor.execute(f"DROP INDEX IF EXISTS {TRIGRAM_INDEX}")
        cursor.execute(
            f"CREATE TEMP TABLE mca_staging (LIKE mca_companies INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        column_list = ", ".join(COLUMNS)
        with cursor.copy(f"COPY mca_staging ({column_list}) FROM STDIN") as copy:
            for count, row in enumerate(read_pages(paths, args.include_closed, skipped), start=1):
                copy.write_row(row)
                if count % 500_000 == 0:
                    print(f"  read {count:,} rows")

        updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in COLUMNS if column != "cin")
        # The same CIN can appear on two pages if the source reorders between requests.
        cursor.execute(
            f"INSERT INTO mca_companies ({column_list}) "
            f"SELECT DISTINCT ON (cin) {column_list} FROM mca_staging ORDER BY cin "
            f"ON CONFLICT (cin) DO UPDATE SET {updates}, loaded_at = now()"
        )
        print(f"  upserted {cursor.rowcount:,} rows, building the search index...")
        cursor.execute(
            f"CREATE INDEX {TRIGRAM_INDEX} ON mca_companies USING gin (normalized_name gin_trgm_ops)"
        )
        connection.commit()

        cursor.execute(
            "SELECT count(*), pg_size_pretty(pg_relation_size('mca_companies')), "
            "pg_size_pretty(pg_indexes_size('mca_companies')), "
            "pg_size_pretty(pg_total_relation_size('mca_companies')) FROM mca_companies"
        )
        rows, table_size, index_size, total_size = cursor.fetchone()
    finally:
        connection.close()

    print(
        f"\nDone in {(time.monotonic() - started) / 60:.1f} min. {rows:,} companies in the table "
        f"(skipped {skipped['closed']:,} closed, {skipped['invalid']:,} invalid). "
        f"Table {table_size} + indexes {index_size} = {total_size} total."
    )


if __name__ == "__main__":
    main()
