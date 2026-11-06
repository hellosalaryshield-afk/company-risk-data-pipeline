from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.news_feed import NewsFeedItem
from app.sources.rss_feeds import FEEDS, Feed, FeedError, FeedItem, fetch_feed


def collect_news_feeds(db: Session, feeds: tuple[Feed, ...] = FEEDS, fetch=fetch_feed) -> dict:
    """Fetch every feed and store articles not seen before. One failing feed never stops
    the others; its error is reported."""
    per_feed: dict[str, dict] = {}
    seen = set()
    for feed in feeds:
        try:
            items = fetch(feed)
        except FeedError as exc:
            per_feed[feed.name] = {"status": "failed", "error": str(exc)}
            continue
        new = store_new(db, [item for item in items if item.url not in seen])
        seen.update(item.url for item in items)
        per_feed[feed.name] = {"status": "ok", "items": len(items), "new": new}
    db.commit()
    return {
        "feeds": per_feed,
        "new_items": sum(f.get("new", 0) for f in per_feed.values()),
        "failed_feeds": sum(1 for f in per_feed.values() if f["status"] == "failed"),
    }


def store_new(db: Session, items: list[FeedItem]) -> int:
    if not items:
        return 0
    existing = set(db.scalars(select(NewsFeedItem.url).where(NewsFeedItem.url.in_([i.url for i in items]))).all())
    fresh = [item for item in items if item.url not in existing]
    for item in fresh:
        db.add(
            NewsFeedItem(
                url=item.url,
                title=item.title,
                summary=item.summary,
                publisher=item.publisher,
                feed=item.feed,
                published_at=item.published_at,
            )
        )
    db.flush()
    return len(fresh)


def search_feed_items(db: Session, names: list[str], days: int, now: datetime | None = None) -> list[NewsFeedItem]:
    """Stored feed articles from the last `days` days whose title contains any of the
    names, newest first. The caller applies the stricter whole-word headline check."""
    now = now or datetime.now(UTC)
    names = [name for name in names if name and len(name.strip()) >= 3]
    if not names:
        return []
    statement = (
        select(NewsFeedItem)
        .where(
            NewsFeedItem.published_at >= now - timedelta(days=days),
            or_(*(NewsFeedItem.title.ilike(f"%{name.strip()}%") for name in names)),
        )
        .order_by(NewsFeedItem.published_at.desc())
    )
    return list(db.scalars(statement).all())
