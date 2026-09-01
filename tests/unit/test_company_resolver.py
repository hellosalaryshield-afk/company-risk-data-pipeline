from app.companies.resolver import CompanyResolver


class FakeRepository:
    def __init__(self):
        self.exact = {}
        self.candidates = []

    def get_by_normalized_alias(self, normalized_alias):
        return self.exact.get(normalized_alias)

    def search_by_alias(self, normalized_query):
        return self.candidates


def test_resolver_returns_exact_match() -> None:
    repository = FakeRepository()
    repository.exact["tcs"] = object()
    resolver = CompanyResolver(repository)

    result = resolver.resolve("TCS")

    assert result.status == "matched"
    assert result.confidence == "high"
    assert result.match is repository.exact["tcs"]


def test_resolver_returns_candidates_for_ambiguous_query() -> None:
    repository = FakeRepository()
    repository.candidates = [object(), object()]
    resolver = CompanyResolver(repository)

    result = resolver.resolve("Tata")

    assert result.status == "ambiguous"
    assert result.confidence == "medium"
    assert len(result.candidates) == 2


def test_resolver_returns_unmatched_for_unknown_query() -> None:
    resolver = CompanyResolver(FakeRepository())

    result = resolver.resolve("Unknown Company")

    assert result.status == "unmatched"
    assert result.confidence == "none"
