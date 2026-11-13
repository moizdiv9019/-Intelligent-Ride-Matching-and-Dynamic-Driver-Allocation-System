from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app


USER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")


class MockResult:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalars(self):
        return self


class MockSession:
    def __init__(self, roles=("admin",)):
        self.roles = list(roles)
        self.calls = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params or {}))
        if "FROM public.user_roles" in sql:
            return MockResult(self.roles)
        if "FROM public.rides AS r" in sql:
            return MockResult(
                [{"lat_index": 1230, "lng_index": 7664, "demand_count": 2}]
            )
        if "FROM public.driver_profiles AS dp" in sql:
            return MockResult(
                [
                    {
                        "lat_index": 1230,
                        "lng_index": 7664,
                        "driver_count": 1,
                        "driver_lat": 12.3047,
                        "driver_lng": 76.6432,
                    }
                ]
            )
        raise AssertionError(f"Unexpected query: {sql}")


@pytest.fixture
def client(monkeypatch):
    db = MockSession()
    current_user = AuthenticatedUser(id=USER_ID, email="admin@example.com")
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: current_user,
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    yield TestClient(app), db
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_zone_summary_requires_admin_role(client):
    api_client, db = client
    db.roles = ["passenger"]

    response = api_client.get("/api/v1/intelligence/zones/summary")

    assert response.status_code == 403
    assert len(db.calls) == 1


def test_admin_zone_summary_returns_aggregates_without_driver_coordinates(client):
    api_client, db = client

    response = api_client.get(
        "/api/v1/intelligence/zones/summary",
        params={"window_minutes": 15, "limit": 10},
    )

    assert response.status_code == 200
    result = response.json()
    assert len(result) == 1
    zone = result[0]
    assert zone["zone_id"] == "zone:1230:7664"
    assert zone["center_lat"] == pytest.approx(12.305)
    assert zone["center_lng"] == pytest.approx(76.645)
    assert zone["bounds"] == {
        "min_lat": 12.3,
        "max_lat": 12.31,
        "min_lng": 76.64,
        "max_lng": 76.65,
    }
    assert zone["recent_demand_count"] == 2
    assert zone["available_driver_count"] == 1
    assert zone["demand_supply_ratio"] == 2
    assert zone["zone_status"] == "shortage"
    assert zone["analysis_timestamp"]
    assert all(
        key not in zone
        for key in ("driver_lat", "driver_lng", "driver_location", "driver_coordinates")
    )
    assert "12.3047" not in response.text
    assert "76.6432" not in response.text

    demand_params = next(
        params
        for sql, params in db.calls
        if "FROM public.rides AS r" in sql
    )
    analysis_timestamp = datetime.fromisoformat(
        zone["analysis_timestamp"].replace("Z", "+00:00")
    )
    assert demand_params["window_start"] == analysis_timestamp - timedelta(minutes=15)
