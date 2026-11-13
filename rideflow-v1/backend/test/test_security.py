from uuid import UUID

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import AuthenticatedUser, get_current_user


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/protected")
    async def protected(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ):
        return current_user.model_dump(mode="json")

    return TestClient(app)


@pytest.fixture
def supabase_config(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co/")
    monkeypatch.setattr(settings, "supabase_publishable_key", "test-publishable-key")


def mock_supabase_response(monkeypatch, response=None, error=None, request_log=None):
    class MockAsyncClient:
        def __init__(self, timeout):
            assert timeout > 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

        async def get(self, url, headers):
            if request_log is not None:
                request_log.update(url=url, headers=headers)
            if error is not None:
                raise error
            return response

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)


def test_missing_authorization_header_returns_401(client):
    response = client.get("/protected")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_returns_401(client, supabase_config, monkeypatch):
    mock_supabase_response(
        monkeypatch,
        response=httpx.Response(401, json={"message": "invalid token"}),
    )

    response = client.get("/protected", headers={"Authorization": "Bearer bad-token"})

    assert response.status_code == 401


def test_valid_token_returns_user_identity_and_forwards_credentials(
    client, supabase_config, monkeypatch
):
    request_log = {}
    mock_supabase_response(
        monkeypatch,
        response=httpx.Response(
            200,
            json={
                "id": "5b2b874a-9b9b-4dd1-8c76-83f173ea0843",
                "email": "rider@example.com",
                "user_metadata": {"display_name": "Rider"},
            },
        ),
        request_log=request_log,
    )

    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer valid-access-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")),
        "email": "rider@example.com",
        "user_metadata": {"display_name": "Rider"},
    }
    assert request_log["url"] == "https://project.supabase.co/auth/v1/user"
    assert request_log["headers"] == {
        "apikey": "test-publishable-key",
        "Authorization": "Bearer valid-access-token",
    }


def test_missing_supabase_configuration_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", None)
    monkeypatch.setattr(settings, "supabase_publishable_key", None)

    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer valid-access-token"},
    )

    assert response.status_code == 503


@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("request timed out"),
        httpx.ConnectError("connection failed"),
    ],
)
def test_supabase_request_failure_returns_503(
    client, supabase_config, monkeypatch, error
):
    mock_supabase_response(monkeypatch, error=error)

    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer valid-access-token"},
    )

    assert response.status_code == 503


def test_supabase_server_failure_returns_503(client, supabase_config, monkeypatch):
    mock_supabase_response(
        monkeypatch,
        response=httpx.Response(500, json={"message": "server error"}),
    )

    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer valid-access-token"},
    )

    assert response.status_code == 503
