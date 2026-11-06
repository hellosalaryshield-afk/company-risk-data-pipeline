from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ScorePeerStat(Base):
    """One peer group's values for one feature, rebuilt by the monthly peer job.

    `feature_key` "__overall__" holds the group's average pre-adjustment score (the sector
    average used for shrinkage) and "__news__" its average news score.
    """

    __tablename__ = "score_peer_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    weights_version: Mapped[str] = mapped_column(String(20), nullable=False)
    group_key: Mapped[str] = mapped_column(String(160), nullable=False)
    feature_key: Mapped[str] = mapped_column(String(80), nullable=False)
    peer_values: Mapped[list[float]] = mapped_column(JSON, nullable=False, default=list)
    average_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    company_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_score_peer_stats_lookup", "weights_version", "group_key", "feature_key"),)


class RiskScore(Base):
    """A score as produced, with its inputs, config version and data date, so it can be
    explained later (Developer Instructions v1, section 10)."""

    __tablename__ = "risk_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    weights_version: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    band: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    coverage: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
