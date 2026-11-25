from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DirectoryCompany(Base):
    """One company from the cleaned taxonomy sheet (data/company_directory.csv).

    The directory is what search covers. A row becomes a scored `Company` the first time
    someone looks it up; `company_id` links the two from then on.
    """

    __tablename__ = "company_directory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    sector: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    industry: Mapped[str] = mapped_column(String(120), nullable=False)
    listing: Mapped[str] = mapped_column(String(20), nullable=False)
    board: Mapped[str | None] = mapped_column(String(20))
    size_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    peer_group: Mapped[str] = mapped_column(String(300), index=True, nullable=False)
    nse_symbol: Mapped[str | None] = mapped_column(String(40), index=True)
    bse_code: Mapped[str | None] = mapped_column(String(20), index=True)
    cin: Mapped[str | None] = mapped_column(String(40), index=True)
    llpin: Mapped[str | None] = mapped_column(String(20))
    funding_stage: Mapped[str | None] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    coverage: Mapped[str] = mapped_column(String(20), nullable=False)
    category_id: Mapped[str | None] = mapped_column(String(20))
    sub_sector: Mapped[str | None] = mapped_column(String(255))
    market_cap_text: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    also_listed_under: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
