from collections.abc import Generator

from fastapi import HTTPException, status
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)

    if not database_url.startswith("postgresql+psycopg://"):
        return database_url

    url = make_url(database_url)
    if "pgbouncer" not in url.query:
        return database_url

    query = {key: value for key, value in url.query.items() if key != "pgbouncer"}
    return url.set(query=query).render_as_string(hide_password=False)


def _create_engine(database_url: str | None) -> Engine | None:
    if not database_url:
        return None

    return create_engine(
        normalize_database_url(database_url),
        pool_pre_ping=True,
        echo=False,
    )


engine = _create_engine(settings.database_url)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db() -> Generator[Session, None, None]:
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured. Set DATABASE_URL to enable database-backed endpoints.",
        )

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
