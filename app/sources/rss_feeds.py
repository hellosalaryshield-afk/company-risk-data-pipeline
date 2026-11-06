"""Publisher RSS feeds as a free news source.

Verified on 2026-11-06: each feed below answered with current items. Moneycontrol's
business feed was left out (its newest item was from April 2024), as was Entrackr's (not
valid XML). Each publisher's terms of use still need checking before production use.
"""

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.sources.article_text import BROWSER_USER_AGENT

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class Feed:
    name: str
    publisher: str
    url: str


FEEDS: tuple[Feed, ...] = (
    Feed("ET companies", "Economic Times", "https://economictimes.indiatimes.com/news/company/rssfeeds/2143429.cms"),
    Feed("ET tech", "Economic Times", "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms"),
    Feed("ET markets", "Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    Feed("Mint companies", "Mint", "https://www.livemint.com/rss/companies"),
    Feed("Mint technology", "Mint", "https://www.livemint.com/rss/technology"),
    Feed("Mint markets", "Mint", "https://www.livemint.com/rss/markets"),
    Feed("Business Standard companies", "Business Standard", "https://www.business-standard.com/rss/companies-101.rss"),
    Feed("BusinessLine companies", "The Hindu BusinessLine", "https://www.thehindubusinessline.com/companies/feeder/default.rss"),
    Feed("Inc42", "Inc42", "https://inc42.com/feed/"),
    Feed("YourStory", "YourStory", "https://yourstory.com/feed"),
    Feed("NDTV Profit", "NDTV Profit", "https://feeds.feedburner.com/ndtvprofit-latest"),
)


@dataclass(frozen=True)
class FeedItem:
    url: str
    title: str
    summary: str | None
    published_at: datetime | None
    feed: str
    publisher: str


class FeedError(RuntimeError):
    pass


def fetch_feed(feed: Feed, timeout_seconds: float = 20.0) -> list[FeedItem]:
    try:
        response = httpx.get(
            feed.url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=timeout_seconds, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        raise FeedError(f"{feed.name}: {exc.__class__.__name__}") from exc
    if response.status_code != 200:
        raise FeedError(f"{feed.name}: HTTP {response.status_code}")
    return parse_feed(response.content, feed)


def parse_feed(content: bytes, feed: Feed) -> list[FeedItem]:
    """RSS 2.0 and Atom. Items without a link or title are skipped."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise FeedError(f"{feed.name}: not valid XML") from exc

    items: list[FeedItem] = []
    for node in root.iter("item"):
        items.append(
            make_item(node.findtext("link"), node.findtext("title"), node.findtext("description"), node.findtext("pubDate"), feed)
        )
    for node in root.iter(f"{ATOM}entry"):
        link = node.find(f"{ATOM}link")
        items.append(
            make_item(
                link.get("href") if link is not None else None,
                node.findtext(f"{ATOM}title"),
                node.findtext(f"{ATOM}summary"),
                node.findtext(f"{ATOM}published") or node.findtext(f"{ATOM}updated"),
                feed,
            )
        )
    return [item for item in items if item is not None]


def make_item(link: str | None, title: str | None, summary: str | None, date: str | None, feed: Feed) -> FeedItem | None:
    link = (link or "").strip()
    title = clean_text(title)
    if not link.startswith("http") or not title:
        return None
    return FeedItem(
        url=link[:1000],
        title=title[:500],
        summary=clean_text(summary) or None,
        published_at=parse_date(date),
        feed=feed.name,
        publisher=feed.publisher,
    )


def clean_text(value: str | None) -> str:
    """Strip HTML tags and entities that feeds put in titles and summaries."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(text).split())


def parse_date(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)  # RSS: "Fri, 06 Nov 2026 14:26:39 +0530"
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom: ISO 8601
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
