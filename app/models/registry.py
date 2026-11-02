from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class McaCompany(Base):
    """One row per company in India's MCA registry, copied in bulk from data.gov.in.

    This is a directory for finding and identifying companies, about 3.7 million rows,
    kept apart from `companies`, which holds only the companies signals are collected
    for. Addresses and capital figures are left out to keep it small.
    """

    __tablename__ = "mca_companies"

    cin: Mapped[str] = mapped_column(String(21), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_status: Mapped[str | None] = mapped_column(String(40))
    company_class: Mapped[str | None] = mapped_column(String(40))
    company_category: Mapped[str | None] = mapped_column(String(80))
    registered_state: Mapped[str | None] = mapped_column(String(60))
    registration_date: Mapped[date | None] = mapped_column(Date)
    listing_status: Mapped[str | None] = mapped_column(String(20))
    industrial_classification: Mapped[str | None] = mapped_column(String(160))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Trigram index for fuzzy name search on PostgreSQL; a plain index elsewhere.
        Index(
            "ix_mca_companies_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )
