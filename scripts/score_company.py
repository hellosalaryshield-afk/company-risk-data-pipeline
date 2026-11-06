import argparse
import json

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.database.session import get_db_session
from app.scoring.service import score_company


def main() -> None:
    parser = argparse.ArgumentParser(description="Score one company and print the result JSON (section 8 of the spec).")
    parser.add_argument("company", help="Company name or alias")
    parser.add_argument("--save", action="store_true", help="Store the score with its inputs in risk_scores")
    parser.add_argument("--full", action="store_true", help="Include inputs and every news event in the output")
    args = parser.parse_args()

    db = next(get_db_session())
    resolved = CompanyResolver(CompanyRepository(db)).resolve(args.company)
    if not resolved.match:
        names = ", ".join(c.canonical_name for c in resolved.candidates) or "none"
        raise SystemExit(f"Company not resolved ({resolved.status}). Candidates: {names}")

    result = score_company(db, resolved.match, persist=args.save)
    if not args.full:
        result = {k: v for k, v in result.items() if k not in {"inputs", "news_events"}}
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
