from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.companies.normalization import normalize_company_name
from app.models.company import Company, CompanyAlias


class CompanyRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_companies(self) -> list[Company]:
        statement = select(Company).options(selectinload(Company.aliases)).order_by(Company.canonical_name)
        return list(self.db.scalars(statement).all())

    def get_company(self, company_id: int) -> Company | None:
        statement = select(Company).options(selectinload(Company.aliases)).where(Company.id == company_id)
        return self.db.scalars(statement).first()

    def get_by_normalized_alias(self, normalized_alias: str) -> Company | None:
        statement = (
            select(Company)
            .join(CompanyAlias)
            .options(selectinload(Company.aliases))
            .where(CompanyAlias.normalized_alias == normalized_alias)
        )
        return self.db.scalars(statement).first()

    def search_by_alias(self, normalized_query: str, limit: int = 5) -> list[Company]:
        if not normalized_query:
            return []

        pattern = f"%{normalized_query}%"
        statement = (
            select(Company)
            .join(CompanyAlias)
            .options(selectinload(Company.aliases))
            .where(CompanyAlias.normalized_alias.ilike(pattern))
            .order_by(Company.canonical_name)
            .limit(limit)
        )
        return list(self.db.scalars(statement).unique().all())

    def create_company(
        self,
        canonical_name: str,
        aliases: list[str] | None = None,
        legal_name: str | None = None,
        country: str | None = None,
        sector: str | None = None,
        industry: str | None = None,
        ticker: str | None = None,
        exchange: str | None = None,
        website: str | None = None,
    ) -> Company:
        company = Company(
            canonical_name=canonical_name,
            legal_name=legal_name,
            country=country,
            sector=sector,
            industry=industry,
            ticker=ticker,
            exchange=exchange,
            website=website,
        )
        self.db.add(company)
        self.db.flush()

        alias_values = [canonical_name, *(aliases or [])]
        seen: set[str] = set()
        for alias in alias_values:
            normalized_alias = normalize_company_name(alias)
            if not normalized_alias or normalized_alias in seen:
                continue
            seen.add(normalized_alias)
            self.db.add(
                CompanyAlias(
                    company_id=company.id,
                    alias=alias.strip(),
                    normalized_alias=normalized_alias,
                    source="manual",
                )
            )

        self.db.commit()
        self.db.refresh(company)
        return company
