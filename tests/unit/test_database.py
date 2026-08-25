from app.database.base import Base
from app.database.session import normalize_database_url
from app.models import Company, CompanyAlias, DataSource


def test_normalize_database_url_uses_psycopg_driver() -> None:
    url = "postgresql://user:password@example.com/database"

    assert normalize_database_url(url) == "postgresql+psycopg://user:password@example.com/database"


def test_normalize_database_url_keeps_explicit_driver() -> None:
    url = "postgresql+psycopg://user:password@example.com/database"

    assert normalize_database_url(url) == url


def test_model_metadata_contains_core_tables() -> None:
    assert Company.__tablename__ in Base.metadata.tables
    assert CompanyAlias.__tablename__ in Base.metadata.tables
    assert DataSource.__tablename__ in Base.metadata.tables
    assert "collection_runs" in Base.metadata.tables
    assert "source_records" in Base.metadata.tables
    assert "kpi_observations" in Base.metadata.tables
