"""add company_directory

Revision ID: 20261125_0008
Revises: 20261116_0007
Create Date: 2026-11-25 00:08:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261125_0008"
down_revision: str | None = "20261116_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_directory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_key", sa.String(length=255), nullable=False),
        sa.Column("sector", sa.String(length=120), nullable=False),
        sa.Column("industry", sa.String(length=120), nullable=False),
        sa.Column("listing", sa.String(length=20), nullable=False),
        sa.Column("board", sa.String(length=20), nullable=True),
        sa.Column("size_tier", sa.String(length=20), nullable=False),
        sa.Column("peer_group", sa.String(length=300), nullable=False),
        sa.Column("nse_symbol", sa.String(length=40), nullable=True),
        sa.Column("bse_code", sa.String(length=20), nullable=True),
        sa.Column("cin", sa.String(length=40), nullable=True),
        sa.Column("llpin", sa.String(length=20), nullable=True),
        sa.Column("funding_stage", sa.String(length=40), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("coverage", sa.String(length=20), nullable=False),
        sa.Column("category_id", sa.String(length=20), nullable=True),
        sa.Column("sub_sector", sa.String(length=255), nullable=True),
        sa.Column("market_cap_text", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("also_listed_under", sa.Text(), nullable=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("name_key", "sector", "peer_group", "nse_symbol", "bse_code", "cin", "company_id"):
        op.create_index(f"ix_company_directory_{column}", "company_directory", [column])


def downgrade() -> None:
    op.drop_table("company_directory")
