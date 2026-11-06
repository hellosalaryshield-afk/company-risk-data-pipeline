from datetime import UTC, datetime, timedelta

import pytest

from app.config.settings import Settings
from app.models.news_feed import NewsFeedItem
from app.pipeline.news_feed_collection import collect_news_feeds, search_feed_items
from app.pipeline.news_signal_collection import discover_articles
from app.sources.rss_feeds import Feed, FeedError, FeedItem, parse_date, parse_feed

FEED = Feed("Test feed", "Test Publisher", "https://feeds.example/rss")

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Companies</title>
<item><title>Zepto cuts 300 jobs &amp; pauses hiring</title><link>https://news.example/zepto-cuts</link>
<description><![CDATA[<p>Quick commerce firm <b>Zepto</b> trims staff.</p>]]></description>
<pubDate>Fri, 06 Nov 2026 14:26:39 +0530</pubDate></item>
<item><title>No link here</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Swiggy shares fall</title><link href="https://news.example/swiggy"/>
<summary>Losses widen</summary><published>2026-11-05T08:22:11Z</published></entry>
</feed>"""


def test_parses_rss_and_strips_html():
    items = parse_feed(RSS, FEED)

    assert len(items) == 1
    item = items[0]
    assert item.title == "Zepto cuts 300 jobs & pauses hiring"
    assert item.summary == "Quick commerce firm Zepto trims staff."
    assert item.published_at == datetime(2026, 11, 6, 8, 56, 39, tzinfo=UTC)
    assert (item.feed, item.publisher) == ("Test feed", "Test Publisher")


def test_parses_atom():
    items = parse_feed(ATOM, FEED)

    assert [i.url for i in items] == ["https://news.example/swiggy"]
    assert items[0].published_at == datetime(2026, 11, 5, 8, 22, 11, tzinfo=UTC)


def test_invalid_xml_is_a_feed_error():
    with pytest.raises(FeedError, match="not valid XML"):
        parse_feed(b"<html>not a feed", FEED)


def test_unparseable_dates_become_none():
    assert parse_date("yesterday") is None
    assert parse_date(None) is None


def item(url, title, days_ago=1):
    return FeedItem(url, title, None, datetime.now(UTC) - timedelta(days=days_ago), "Test feed", "Test Publisher")


def test_collection_stores_new_items_once_and_survives_a_failing_feed(db_session):
    good = Feed("Good", "P", "https://g.example")
    bad = Feed("Bad", "P", "https://b.example")
    batches = {"Good": [item("https://n.example/1", "Zepto raises funds"), item("https://n.example/2", "Other")]}

    def fetch(feed):
        if feed.name == "Bad":
            raise FeedError("Bad: HTTP 503")
        return batches[feed.name]

    first = collect_news_feeds(db_session.session, (good, bad), fetch=fetch)
    second = collect_news_feeds(db_session.session, (good, bad), fetch=fetch)

    assert first["new_items"] == 2 and second["new_items"] == 0
    assert first["feeds"]["Bad"] == {"status": "failed", "error": "Bad: HTTP 503"}
    assert db_session.session.query(NewsFeedItem).count() == 2


def store(db_session, url, title, days_ago):
    db_session.session.add(
        NewsFeedItem(url=url, title=title, publisher="Mint", feed="Mint companies",
                     published_at=datetime.now(UTC) - timedelta(days=days_ago))
    )
    db_session.session.flush()


def test_search_finds_recent_titles_naming_the_company(db_session):
    store(db_session, "https://n.example/1", "Zepto cuts 300 jobs", 2)
    store(db_session, "https://n.example/2", "Zepto expands to Pune", 45)
    store(db_session, "https://n.example/3", "Blinkit grows", 1)

    found = search_feed_items(db_session.session, ["Zepto", "Kiranakart"], days=30)

    assert [i.url for i in found] == ["https://n.example/1"]


def test_rss_discovery_keeps_whole_name_matches_only(db_session):
    store(db_session, "https://n.example/1", "Kiranakart files IPO papers", 1)
    store(db_session, "https://n.example/2", "Zeptonow rival launches app", 1)  # not "Zepto"

    articles, notes = discover_articles(
        "Zepto", Settings(), discovery="rss", headline_names=["Zepto", "Kiranakart"], db=db_session.session
    )

    assert [a.url for a in articles] == ["https://n.example/1"]
    assert articles[0].discovered_via == "rss:Mint companies"
    assert notes == []


def test_auto_discovery_uses_rss_before_any_api(db_session, monkeypatch):
    from app.pipeline import news_signal_collection

    def must_not_call(*args, **kwargs):
        raise AssertionError("GDELT was called although RSS had articles.")

    monkeypatch.setattr(news_signal_collection.GdeltClient, "fetch_article_list", must_not_call)
    store(db_session, "https://n.example/1", "Zepto cuts 300 jobs", 1)

    articles, _ = discover_articles("Zepto", Settings(), db=db_session.session)

    assert [a.discovered_via for a in articles] == ["rss:Mint companies"]
