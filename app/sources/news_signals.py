import re

LAYOFF_PATTERNS = [
    r"\blayoffs?\b",
    r"\bfirings?\b",
    r"\bjob cuts?\b",
    r"\bworkforce reduction\b",
    r"\bdownsizing\b",
    r"\bheadcount reduction\b",
]

RESTRUCTURING_PATTERNS = [
    r"\brestructur(?:e|ing|ed)\b",
    r"\brealignment\b",
    r"\bcost rationali[sz]ation\b",
    r"\bcost cutting\b",
    r"\boperational efficiency\b",
]

FUNDING_PATTERNS = [
    r"\bfunding\b",
    r"\bfundraise\b",
    r"\braised\b",
    r"\binvestment\b",
    r"\bseries [a-z]\b",
]

DISTRESS_PATTERNS = [
    *LAYOFF_PATTERNS,
    *RESTRUCTURING_PATTERNS,
    r"\bloss(?:es)?\b",
    r"\bcrisis\b",
    r"\bdefault\b",
    r"\bshutdown\b",
    r"\binsolvency\b",
]


def count_matching_articles(articles_text: list[str], patterns: list[str]) -> int:
    combined = re.compile("|".join(patterns), flags=re.IGNORECASE)
    return sum(1 for text in articles_text if combined.search(text))


def extract_news_kpis(articles_text: list[str]) -> dict[str, int]:
    return {
        "news_article_count": len(articles_text),
        "layoff_news_count": count_matching_articles(articles_text, LAYOFF_PATTERNS),
        "restructuring_news_count": count_matching_articles(articles_text, RESTRUCTURING_PATTERNS),
        "funding_news_count": count_matching_articles(articles_text, FUNDING_PATTERNS),
        "distress_news_count": count_matching_articles(articles_text, DISTRESS_PATTERNS),
    }
