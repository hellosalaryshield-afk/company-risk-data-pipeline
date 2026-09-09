import argparse
import sys
from datetime import UTC, datetime

from app.database.session import get_db_session
from app.features.point_in_time import leakage_scan

# The brief requires: "A leakage check run against the final feature set before any modeling
# starts - interns to get a sign off from mentor". This is that check. Run it, read it with
# the mentor, and only then start Phase 4.


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Check every stored KPI for point-in-time problems before modelling."
    )
    parser.add_argument(
        "--as-of",
        help="Prediction date to test against, ISO format, for example 2026-06-01. Defaults to now.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any observation has an issue, not only impossible timestamps.",
    )
    args = parser.parse_args()

    as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=UTC) if args.as_of else datetime.now(UTC)

    db = next(get_db_session())
    report = leakage_scan(db, as_of=as_of)

    print(f"Point-in-time leakage check as of {report['as_of'][:19]}")
    print(f"  observations checked : {report['observations_checked']}")
    print(f"  point-in-time safe   : {report['point_in_time_safe']}")
    print(f"  with issues          : {report['with_issues']}")
    print()

    if report["impossible_timestamps"]:
        print(f"IMPOSSIBLE TIMESTAMPS: {len(report['impossible_timestamps'])}")
        print("  A value cannot be collected before it was published. These rows are corrupt")
        print("  and nothing downstream of them can be trusted.")
        for row in report["impossible_timestamps"][:5]:
            print(
                f"    {row['kpi_name']:34} source={row['source']} "
                f"published={row['published_at'][:19]} collected={row['collected_at'][:19]}"
            )
        if len(report["impossible_timestamps"]) > 5:
            print(f"    ... and {len(report['impossible_timestamps']) - 5} more")
        print()

    if report["findings"]:
        print("Findings, most common first:")
        for finding in report["findings"]:
            print(f"  [{finding['count']:>4}] {finding['issue']}")
            print(f"         sources : {', '.join(s or '?' for s in finding['sources'])}")
            print(
                f"         kpis    : {finding['kpis_affected']} affected, "
                f"e.g. {', '.join(finding['kpi_sample'][:3])}"
            )
        print()
    else:
        print("No point-in-time issues found.\n")

    print("Reminder: the brief requires mentor sign-off on this check before Phase 4 modelling.")

    if report["impossible_timestamps"]:
        raise SystemExit(2)
    if args.strict and report["with_issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
