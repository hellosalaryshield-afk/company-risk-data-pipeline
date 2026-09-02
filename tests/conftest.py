from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.companies.repository import CompanyRepository
from app.database.base import Base
from app.models import *  # noqa: F403


@dataclass
class DatabaseSessionFixture:
    session: Session

    def create_company(self, canonical_name: str, aliases: list[str] | None = None):
        return CompanyRepository(self.session).create_company(
            canonical_name=canonical_name,
            aliases=aliases or [],
            country="India",
            sector="Test Sector",
        )


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    session = TestingSessionLocal()
    try:
        yield DatabaseSessionFixture(session=session)
    finally:
        session.close()
        Base.metadata.drop_all(engine)
