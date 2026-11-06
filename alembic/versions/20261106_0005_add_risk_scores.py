"""add risk_scores and score_peer_stats

Revision ID: 20261106_0005
Revises: 20261030_0004
Create Date: 2026-11-06 00:05:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261106_0005"
down_revision: str | None = "20261030_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "score_peer_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("weights_version", sa.String(length=20), nullable=False),
        sa.Column("group_key", sa.String(length=160), nullable=False),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("peer_values", sa.JSON(), nullable=False),
        sa.Column("average_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("company_count", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_score_peer_stats_lookup", "score_peer_stats", ["weights_version", "group_key", "feature_key"]
    )
    op.create_table(
        "risk_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weights_version", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column("band", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("coverage", sa.Numeric(6, 4), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_risk_scores_company_id", "risk_scores", ["company_id"])
    op.create_index("ix_risk_scores_computed_at", "risk_scores", ["computed_at"])


def downgrade() -> None:
    op.drop_index("ix_risk_scores_computed_at", table_name="risk_scores")
    op.drop_index("ix_risk_scores_company_id", table_name="risk_scores")
    op.drop_table("risk_scores")
    op.drop_index("ix_score_peer_stats_lookup", table_name="score_peer_stats")
    op.drop_table("score_peer_stats")
