import sys

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.database.session import get_db_session


def main() -> None:
    query = " ".join(sys.argv[1:]).strip() or "TCS"
    db = next(get_db_session())
    result = CompanyResolver(CompanyRepository(db)).resolve(query)

    print(f"Query: {result.query}")
    print(f"Status: {result.status}")
    print(f"Confidence: {result.confidence}")

    if result.match:
        print(f"Match: {result.match.canonical_name} (id={result.match.id})")
    elif result.candidates:
        print("Candidates:")
        for candidate in result.candidates:
            print(f"- {candidate.canonical_name} (id={candidate.id})")
    else:
        print("No match found.")


if __name__ == "__main__":
    main()
