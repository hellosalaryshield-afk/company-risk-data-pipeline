from dataclasses import dataclass

import httpx
import trafilatura

# Many news sites refuse requests that do not look like a browser.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# A news page larger than this is almost certainly not a single article.
MAX_PAGE_BYTES = 3_000_000

# Text shorter than this is usually a paywall stub, a cookie notice or a video page.
MIN_ARTICLE_CHARS = 300


@dataclass(frozen=True)
class ArticleText:
    url: str
    text: str | None
    status: str
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.text is not None


class ArticleTextFetcher:
    """Download a news page and keep only the article body.

    The full text is used for classification and then discarded. It is never stored,
    because the pipeline has no licence to republish article content.
    """

    def __init__(self, timeout_seconds: float = 15.0, max_chars: int = 6000):
        self.timeout_seconds = timeout_seconds
        self.max_chars = max_chars

    def fetch(self, url: str) -> ArticleText:
        try:
            response = httpx.get(
                url,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": BROWSER_USER_AGENT, "Accept-Language": "en"},
            )
        except httpx.TimeoutException:
            return ArticleText(url=url, text=None, status="timeout")
        except httpx.HTTPError as exc:
            return ArticleText(url=url, text=None, status="network_error", detail=exc.__class__.__name__)

        if response.status_code != 200:
            return ArticleText(url=url, text=None, status="http_error", detail=str(response.status_code))

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            return ArticleText(url=url, text=None, status="not_html", detail=content_type or None)

        if len(response.content) > MAX_PAGE_BYTES:
            return ArticleText(url=url, text=None, status="too_large")

        return self.extract(url, response.text)

    def extract(self, url: str, html: str) -> ArticleText:
        text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True)
        if not text or len(text.strip()) < MIN_ARTICLE_CHARS:
            return ArticleText(url=url, text=None, status="no_article_text")

        return ArticleText(url=url, text=text.strip()[: self.max_chars], status="ok")
