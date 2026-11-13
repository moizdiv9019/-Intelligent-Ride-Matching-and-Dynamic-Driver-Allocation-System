from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app


USER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
OTHER_USER_ID = UUID("c6de2a96-253c-45f1-bdb2-a32682a9e3cf")
CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
UPDATED_AT = datetime(2026, 1, 2, tzinfo=timezone.utc)


def profile_row(**overrides):
    profile = {
        "id": USER_ID,
        "full_name": "RideFlow User",
        "phone_number": "+15551234567",
        "avatar_url": "https://example.com/avatar.png",
        "account_status": "active",
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }
    profile.update(overrides)
    return profile


class MockResult:
    def __init__(self, *, row=None, values=()):
        self.row = row
        self.values = list(values)

    def mappings(self):
        return self

    def first(self):
        return self.row

    def one(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return self.values


class MockSession:
    def __init__(self, *, profile=None, roles=(), upserted_profile=None):
        self.profile = profile
        self.roles = list(roles)
        self.upserted_profile = upserted_profile or profile_row()
        self.execute_calls = []
        self.commit_count = 0

    def execute(self, statement, params):
        sql = str(statement)
        self.execute_calls.append((sql, params))

        if "INSERT INTO public.profiles" in sql:
            return MockResult(row=self.upserted_profile)
        if "FROM public.profiles" in sql:
            return MockResult(row=self.profile)
        if "FROM public.user_roles" in sql:
            return MockResult(values=self.roles)
        raise AssertionError(f"Unexpected SQL statement: {sql}")

    def commit(self):
        self.commit_count += 1


@pytest.fixture
def db():
    return MockSession(profile=profile_row(), roles=("driver", "passenger"))


@pytest.fixture
def client(monkeypatch, db):
    current_user = AuthenticatedUser(id=USER_ID, email="user@example.com")
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: current_user,
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app)


def test_authenticated_user_can_retrieve_profile_and_roles(client, db):
    response = client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(USER_ID),
        "email": "user@example.com",
        "profile": {
            "id": str(USER_ID),
            "full_name": "RideFlow User",
            "phone_number": "+15551234567",
            "avatar_url": "https://example.com/avatar.png",
            "account_status": "active",
            "created_at": CREATED_AT.isoformat().replace("+00:00", "Z"),
            "updated_at": UPDATED_AT.isoformat().replace("+00:00", "Z"),
        },
        "roles": ["driver", "passenger"],
    }
    assert db.execute_calls[0][1] == {"user_id": USER_ID}
    assert db.execute_calls[1][1] == {"user_id": USER_ID}


def test_user_without_profile_gets_null_profile(client, db):
    db.profile = None
    db.roles = []

    response = client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json()["profile"] is None
    assert response.json()["roles"] == []


def test_profile_creation_uses_authenticated_user_id(client, db):
    response = client.put(
        "/api/v1/me/profile",
        json={
            "full_name": "New Rider",
            "phone_number": "+15550001111",
            "avatar_url": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(USER_ID)
    assert db.commit_count == 1
    sql, params = db.execute_calls[0]
    assert "INSERT INTO public.profiles" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert params == {
        "user_id": USER_ID,
        "full_name": "New Rider",
        "phone_number": "+15550001111",
        "avatar_url": None,
    }


def test_profile_update_changes_allowed_fields_and_updated_at(client, db):
    db.upserted_profile = profile_row(
        full_name="Updated Rider",
        phone_number=None,
        account_status="restricted",
    )

    response = client.put(
        "/api/v1/me/profile",
        json={"full_name": "Updated Rider", "phone_number": None},
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Updated Rider"
    assert response.json()["account_status"] == "restricted"
    sql, _ = db.execute_calls[0]
    assert "updated_at = now()" in sql
    assert "account_status =" not in sql


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", str(OTHER_USER_ID)),
        ("user_id", str(OTHER_USER_ID)),
    ],
)
def test_user_cannot_update_another_users_profile(client, db, field, value):
    response = client.put(
        "/api/v1/me/profile",
        json={"full_name": "Name", field: value},
    )

    assert response.status_code == 422
    assert db.execute_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_status", "suspended"),
        ("roles", ["admin"]),
    ],
)
def test_user_cannot_edit_account_status_or_roles(client, db, field, value):
    response = client.put(
        "/api/v1/me/profile",
        json={"full_name": "Name", field: value},
    )

    assert response.status_code == 422
    assert db.execute_calls == []


def test_role_dependency_returns_403_for_unauthorized_role(monkeypatch, db):
    db.roles = ["passenger"]
    app_with_role = FastAPI()

    @app_with_role.get("/driver-only")
    def driver_only(user: AuthenticatedUser = Depends(require_roles("driver"))):
        return {"id": str(user.id)}

    monkeypatch.setitem(
        app_with_role.dependency_overrides,
        get_current_user,
        lambda: AuthenticatedUser(id=USER_ID, email="user@example.com"),
    )
    monkeypatch.setitem(app_with_role.dependency_overrides, get_db, lambda: db)

    response = TestClient(app_with_role).get("/driver-only")

    assert response.status_code == 403
    sql, params = db.execute_calls[0]
    assert "FROM public.user_roles" in sql
    assert params == {"user_id": USER_ID}
