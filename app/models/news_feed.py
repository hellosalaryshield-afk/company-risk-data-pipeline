from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class NewsFeedItem(Base):
    """One article seen in a publisher's RSS feed.

    Feeds only show their latest items, so they are collected several times a day and
    kept here; news discovery then searches this table instead of a news API.
    """

    __tablename__ = "news_feed_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(String(120), nullable=False)
    feed: Mapped[str] = mapped_column(String(120), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
