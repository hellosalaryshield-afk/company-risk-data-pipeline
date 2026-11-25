"""company_directory: country, foreign listing and aliases

Revision ID: 20261125_0009
Revises: 20261125_0008
Create Date: 2026-11-25 00:09:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261125_0009"
down_revision: str | None = "20261125_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("company_directory", sa.Column("country", sa.String(length=60), nullable=False, server_default="India"))
    op.add_column("company_directory", sa.Column("foreign_ticker", sa.String(length=20), nullable=True))
    op.add_column("company_directory", sa.Column("foreign_exchange", sa.String(length=20), nullable=True))
    op.add_column("company_directory", sa.Column("aliases", sa.Text(), nullable=True))
    op.create_index("ix_company_directory_foreign_ticker", "company_directory", ["foreign_ticker"])


def downgrade() -> None:
    op.drop_index("ix_company_directory_foreign_ticker", table_name="company_directory")
    for column in ("aliases", "foreign_exchange", "foreign_ticker", "country"):
        op.drop_column("company_directory", column)
