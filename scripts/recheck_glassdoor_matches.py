"""Re-check Glassdoor answers that were rejected as "a different company", with today's rules.

    python scripts/recheck_glassdoor_matches.py            # show what would change
    python scripts/recheck_glassdoor_matches.py --apply    # change it

For each company whose latest Glassdoor answer was rejected:
  - if the stored answer now passes (for example it is one of the company's known names),
    its ratings are stored from that answer. No new Apify call, so nothing is billed.
  - if it still fails but the company would now be searched under a different name (a US
    company searched by its brand), the old answer is set aside so the next website search
    asks Glassdoor again, which costs one call.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import desc, select  # noqa: E402

from app.config.settings import get_settings  # noqa: E402
from app.database.session import get_db_session  # noqa: E402
from app.models.collection import CollectionRun, SourceRecord  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.pipeline.workplace_collection import (  # noqa: E402
    WORKPLACE_SOURCE_NAME,
    collect_workplace_for_company,
    glassdoor_search_name,
    known_names,
)
from app.sources.glassdoor import match_confidence  # noqa: E402

SET_ASIDE = "superseded"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: only show them)")
    args = parser.parse_args()
    db = next(get_db_session())
    settings = get_settings()
    try:
        runs = db.scalars(
            select(CollectionRun)
            .where(CollectionRun.extra_metadata["source"].as_string() == WORKPLACE_SOURCE_NAME)
            .order_by(desc(CollectionRun.started_at))
        ).all()
        latest: dict[int, CollectionRun] = {}
        for run in runs:
            if run.status == "completed":
                latest.setdefault(run.company_id, run)

        for company_id, run in latest.items():
            if not (run.extra_metadata or {}).get("match_rejected"):
                continue
            company = db.get(Company, company_id)
            record = db.scalars(select(SourceRecord).where(SourceRecord.collection_run_id == run.id)).first()
            if company is None or record is None:
                continue
            search_name = glassdoor_search_name(db, company)
            confidence, reason = match_confidence(
                company.canonical_name, record.raw_data, known_names(company, search_name)
            )
            searched_before = run.extra_metadata.get("searched_as") or run.extra_metadata.get("query")
            if confidence != "low":
                print(f"{company.canonical_name}: accept stored answer ({confidence}): {reason}")
                if args.apply:
                    result = collect_workplace_for_company(
                        db, company.canonical_name, settings, raw_record=record.raw_data, reused_from_run_id=run.id
                    )
                    print(f"  stored {len(result.get('kpis') or {})} ratings")
            elif search_name != searched_before:
                print(f"{company.canonical_name}: still rejected; next search asks Glassdoor for '{search_name}'")
                if args.apply:
                    run.status = SET_ASIDE
                    run.extra_metadata = {**run.extra_metadata, "set_aside": f"search again as '{search_name}'"}
                    db.commit()
            else:
                print(f"{company.canonical_name}: still rejected ({reason})")
        if not args.apply:
            print("\nNothing changed. Run again with --apply to make these changes.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
