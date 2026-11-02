"""add mca_companies registry directory

Revision ID: 20261030_0004
Revises: 20260908_0003
Create Date: 2026-10-30 00:04:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261030_0004"
down_revision: str | None = "20260908_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "mca_companies",
        sa.Column("cin", sa.String(length=21), primary_key=True),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("company_status", sa.String(length=40), nullable=True),
        sa.Column("company_class", sa.String(length=40), nullable=True),
        sa.Column("company_category", sa.String(length=80), nullable=True),
        sa.Column("registered_state", sa.String(length=60), nullable=True),
        sa.Column("registration_date", sa.Date(), nullable=True),
        sa.Column("listing_status", sa.String(length=20), nullable=True),
        sa.Column("industrial_classification", sa.String(length=160), nullable=True),
        sa.Column("loaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_mca_companies_normalized_name_trgm",
        "mca_companies",
        ["normalized_name"],
        postgresql_using="gin",
        postgresql_ops={"normalized_name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_mca_companies_normalized_name_trgm", table_name="mca_companies")
    op.drop_table("mca_companies")
