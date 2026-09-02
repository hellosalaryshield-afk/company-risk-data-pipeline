"""add company identity fields

Revision ID: 20260902_0002
Revises: 20260825_0001
Create Date: 2026-09-02 00:02:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260902_0002"
down_revision: str | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("cin", sa.String(length=40), nullable=True))
    op.add_column("companies", sa.Column("incorporation_date", sa.Date(), nullable=True))
    op.add_column("companies", sa.Column("company_status", sa.String(length=120), nullable=True))
    op.add_column("companies", sa.Column("company_category", sa.String(length=160), nullable=True))
    op.create_index("ix_companies_cin", "companies", ["cin"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_companies_cin", table_name="companies")
    op.drop_column("companies", "company_category")
    op.drop_column("companies", "company_status")
    op.drop_column("companies", "incorporation_date")
    op.drop_column("companies", "cin")
