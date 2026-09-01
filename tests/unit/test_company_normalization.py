from app.companies.normalization import normalize_company_name


def test_normalize_company_name_removes_legal_suffixes() -> None:
    assert normalize_company_name("Tata Consultancy Services Ltd.") == "tata consultancy services"


def test_normalize_company_name_handles_symbols_and_spacing() -> None:
    assert normalize_company_name("  TCS   Private & Limited ") == "tcs and"


def test_normalize_company_name_preserves_meaningful_words() -> None:
    assert normalize_company_name("Ola Electric Mobility Limited") == "ola electric mobility"
