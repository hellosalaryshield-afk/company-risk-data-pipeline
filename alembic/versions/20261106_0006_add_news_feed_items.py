"""add news_feed_items

Revision ID: 20261106_0006
Revises: 20261106_0005
Create Date: 2026-11-06 00:06:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261106_0006"
down_revision: str | None = "20261106_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "news_feed_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(length=1000), nullable=False, unique=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("publisher", sa.String(length=120), nullable=False),
        sa.Column("feed", sa.String(length=120), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_news_feed_items_published_at", "news_feed_items", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_news_feed_items_published_at", table_name="news_feed_items")
    op.drop_table("news_feed_items")
