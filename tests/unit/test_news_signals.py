from app.sources.news_signals import extract_news_kpis


def test_extract_news_kpis_counts_signal_families() -> None:
    texts = [
        "Company announces layoffs and workforce reduction",
        "Company raised Series B funding",
        "Company begins cost rationalization plan",
        "Company launches new product",
    ]

    kpis = extract_news_kpis(texts)

    assert kpis["news_article_count"] == 4
    assert kpis["layoff_news_count"] == 1
    assert kpis["restructuring_news_count"] == 1
    assert kpis["funding_news_count"] == 1
    assert kpis["distress_news_count"] == 2
