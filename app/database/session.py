from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def create_database_engine():
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return create_engine(normalize_database_url(settings.database_url), pool_pre_ping=True)


engine = None
SessionLocal: sessionmaker[Session] | None = None


def initialize_database() -> None:
    global engine, SessionLocal
    engine = create_database_engine()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db_session() -> Generator[Session, None, None]:
    if SessionLocal is None:
        initialize_database()
    if SessionLocal is None:
        raise RuntimeError("Database session factory could not be initialized.")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
