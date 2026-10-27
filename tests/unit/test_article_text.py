import httpx

from app.sources import article_text
from app.sources.article_text import ArticleTextFetcher

PARAGRAPHS = (
    "Zepto said on Tuesday that it would reduce its workforce by about 300 people as part of a "
    "wider effort to cut costs ahead of a planned listing.",
    "The company told staff in an internal note that the affected roles were mostly in "
    "operations and customer support, and that severance would be paid.",
    "Analysts said the move reflected pressure across quick commerce to show a path to "
    "profitability after several years of rapid, heavily funded expansion.",
)

ARTICLE_HTML = f"""<html><head><title>Zepto to cut 300 jobs</title></head><body>
<nav>Home | Business | Markets</nav>
<article><h1>Zepto to cut 300 jobs</h1>{"".join(f"<p>{p}</p>" for p in PARAGRAPHS)}</article>
<footer>Copyright 2026</footer></body></html>"""


def respond(status_code=200, text=ARTICLE_HTML, content_type="text/html; charset=utf-8"):
    def get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(status_code, text=text, headers={"content-type": content_type}, request=request)

    return get


def test_extracts_the_article_body(monkeypatch):
    monkeypatch.setattr(article_text.httpx, "get", respond())

    result = ArticleTextFetcher().fetch("https://news.example/zepto")

    assert result.ok
    assert result.status == "ok"
    assert "reduce its workforce by about 300 people" in result.text
    assert "Home | Business" not in result.text


def test_text_is_capped(monkeypatch):
    monkeypatch.setattr(article_text.httpx, "get", respond())

    result = ArticleTextFetcher(max_chars=100).fetch("https://news.example/zepto")

    assert len(result.text) == 100


def test_a_stub_page_has_no_article_text(monkeypatch):
    monkeypatch.setattr(article_text.httpx, "get", respond(text="<html><body><p>Subscribe to read.</p></body></html>"))

    result = ArticleTextFetcher().fetch("https://news.example/paywall")

    assert not result.ok
    assert result.status == "no_article_text"


def test_http_errors_and_non_html_are_reported(monkeypatch):
    monkeypatch.setattr(article_text.httpx, "get", respond(status_code=403))
    assert ArticleTextFetcher().fetch("https://x.example").status == "http_error"

    monkeypatch.setattr(article_text.httpx, "get", respond(content_type="application/pdf"))
    assert ArticleTextFetcher().fetch("https://x.example").status == "not_html"


def test_network_failures_are_reported(monkeypatch):
    def timeout(url, **kwargs):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(article_text.httpx, "get", timeout)
    assert ArticleTextFetcher().fetch("https://x.example").status == "timeout"

    def refused(url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(article_text.httpx, "get", refused)
    result = ArticleTextFetcher().fetch("https://x.example")
    assert result.status == "network_error"
    assert result.detail == "ConnectError"
