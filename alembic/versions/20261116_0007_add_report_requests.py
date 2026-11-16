"""add report_requests

Revision ID: 20261116_0007
Revises: 20261106_0006
Create Date: 2026-11-16 00:07:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261116_0007"
down_revision: str | None = "20261106_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_report_requests_email", "report_requests", ["email"])
    op.create_index("ix_report_requests_company_id", "report_requests", ["company_id"])
    op.create_index("ix_report_requests_status", "report_requests", ["status"])
    op.create_index("ix_report_requests_created_at", "report_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_report_requests_created_at", table_name="report_requests")
    op.drop_index("ix_report_requests_status", table_name="report_requests")
    op.drop_index("ix_report_requests_company_id", table_name="report_requests")
    op.drop_index("ix_report_requests_email", table_name="report_requests")
    op.drop_table("report_requests")
