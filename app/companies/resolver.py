from dataclasses import dataclass

from app.companies.normalization import normalize_company_name
from app.companies.repository import CompanyRepository
from app.models.company import Company


@dataclass(frozen=True)
class ResolveResult:
    status: str
    query: str
    normalized_query: str
    match: Company | None
    candidates: list[Company]
    confidence: str


class CompanyResolver:
    def __init__(self, repository: CompanyRepository):
        self.repository = repository

    def resolve(self, query: str) -> ResolveResult:
        normalized_query = normalize_company_name(query)
        if not normalized_query:
            return ResolveResult(
                status="unmatched",
                query=query,
                normalized_query=normalized_query,
                match=None,
                candidates=[],
                confidence="none",
            )

        exact_match = self.repository.get_by_normalized_alias(normalized_query)
        if exact_match:
            return ResolveResult(
                status="matched",
                query=query,
                normalized_query=normalized_query,
                match=exact_match,
                candidates=[],
                confidence="high",
            )

        candidates = self.repository.search_by_alias(normalized_query)
        return ResolveResult(
            status="ambiguous" if candidates else "unmatched",
            query=query,
            normalized_query=normalized_query,
            match=None,
            candidates=candidates,
            confidence="medium" if candidates else "none",
        )
