from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ReportRequest(Base):
    """Someone on the website asked for a company's full scorecard by email.

    The report only reaches them if the address is real, so each sent row is a verified
    lead. `consent` records that they agreed to be contacted about Salary Shield.
    """

    __tablename__ = "report_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), index=True, nullable=False)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # pending -> sent | saved (no mail server configured, written to the outbox) | failed
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
