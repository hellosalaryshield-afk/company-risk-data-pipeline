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

    def create_company(self, canonical_name: str, aliases: list[str] | None = None, **overrides):
        fields = {"country": "India", "sector": "Test Sector"}
        fields.update(overrides)
        return CompanyRepository(self.session).create_company(
            canonical_name=canonical_name,
            aliases=aliases or [],
            **fields,
        )


@pytest.fixture(autouse=True)
def no_gdelt_throttle_or_network(monkeypatch):
    """Keep the GDELT throttle out of the test suite.

    The real client waits eight seconds between calls, which would make the suite
    unusable, and an un-stubbed test would reach the live API. Both are disabled here.
    """
    from app.sources import gdelt

    monkeypatch.setattr(gdelt._THROTTLE, "min_interval_seconds", 0.0)

    def refuse(*args, **kwargs):
        raise AssertionError("A test tried to call GDELT over the network. Stub it instead.")

    monkeypatch.setattr(gdelt.httpx, "get", refuse)


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
