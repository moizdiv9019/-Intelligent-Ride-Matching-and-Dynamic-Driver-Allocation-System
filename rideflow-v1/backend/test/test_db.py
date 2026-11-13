from importlib import reload

import pytest
from fastapi import HTTPException

from app.core import config
from app.db.base import Base
from app.db import session as db_session


def test_base_is_importable():
    assert Base is not None
    assert Base.metadata.tables == {}


@pytest.mark.parametrize(
    ("database_url", "expected_url"),
    [
        ("postgresql://example/db", "postgresql+psycopg://example/db"),
        ("postgres://example/db", "postgresql+psycopg://example/db"),
        (
            "postgresql+psycopg://example/db",
            "postgresql+psycopg://example/db",
        ),
        (
            "postgresql://example/db?pgbouncer=true&sslmode=require",
            "postgresql+psycopg://example/db?sslmode=require",
        ),
    ],
)
def test_normalize_database_url(database_url, expected_url):
    assert db_session.normalize_database_url(database_url) == expected_url


def test_missing_database_url_is_handled_safely(monkeypatch):
    configured_database_url = config.settings.database_url
    monkeypatch.setattr(config.settings, "database_url", "")

    try:
        reload(db_session)

        assert db_session.engine is None
        with pytest.raises(HTTPException) as exception_info:
            next(db_session.get_db())

        assert exception_info.value.status_code == 503
        assert "DATABASE_URL" in exception_info.value.detail
    finally:
        monkeypatch.setattr(config.settings, "database_url", configured_database_url)
        reload(db_session)
