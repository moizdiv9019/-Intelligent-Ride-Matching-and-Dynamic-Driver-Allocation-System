from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.services.repositioning_service import RepositioningRecommendation
from app.services.surge_pricing_service import SurgeZone


USER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
DRIVER_ID = UUID("d4a9db7d-e62c-4c82-a0b1-e9f345217d55")


class MockResult:
    def __init__(self, values=()):
        self.values = list(values)

    def scalars(self):
        return self

    def all(self):
        return self.values


class MockSession:
    def __init__(self, roles=("admin",)):
        self.roles = list(roles)
        self.statements = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        assert "FROM public.user_roles" in sql
        return MockResult(self.roles)


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


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/intelligence/zones/surge",
        "/api/v1/intelligence/repositioning/recommendations",
    ],
)
def test_passenger_cannot_access_surge_or_repositioning(client, path):
    api_client, db = client
    db.roles = ["passenger"]

    response = api_client.get(path)

    assert response.status_code == 403
    assert len(db.statements) == 1


def test_admin_surge_endpoint_returns_simulated_zone_analysis(
    client,
    monkeypatch,
):
    from app.api.v1.endpoints import zone_intelligence

    api_client, _ = client
    monkeypatch.setattr(
        zone_intelligence,
        "generate_surge_analysis",
        lambda db, **kwargs: [
            SurgeZone(
                zone_id="zone:1229:7664",
                center_lat=12.295,
                center_lng=76.645,
                predicted_demand=8,
                actual_recent_demand=2,
                available_driver_count=2,
                projected_demand_supply_ratio=4.0,
                surge_multiplier=1.25,
                simulated_fare_adjustment_percentage=25.0,
                explanation="Projected shortage; simulated only.",
            )
        ],
    )

    response = api_client.get(
        "/api/v1/intelligence/zones/surge",
        params={"hours_ahead": 3, "limit": 25},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["forecast_horizon_hours"] == 3
    assert body["data_source"] == "synthetic_simulation"
    assert body["simulated_only"] is True
    assert body["zones"][0]["zone_id"] == "zone:1229:7664"
    assert body["zones"][0]["surge_multiplier"] == 1.25
    assert body["zones"][0]["simulated_fare_adjustment_percentage"] == 25
    assert body["zones"][0]["explanation"]


def test_admin_repositioning_endpoint_returns_recommendations_without_gps(
    client,
    monkeypatch,
):
    from app.api.v1.endpoints import zone_intelligence

    api_client, _ = client
    monkeypatch.setattr(
        zone_intelligence,
        "generate_repositioning_recommendations",
        lambda db, **kwargs: [
            RepositioningRecommendation(
                driver_id=DRIVER_ID,
                current_zone_id="zone:1228:7663",
                recommended_zone_id="zone:1229:7664",
                target_center_lat=12.295,
                target_center_lng=76.645,
                approximate_distance_meters=1500,
                forecasted_target_demand=10,
                target_driver_supply=0,
                reason="Forecast shortage; straight-line estimate.",
            )
        ],
    )

    response = api_client.get(
        "/api/v1/intelligence/repositioning/recommendations",
        params={"hours_ahead": 6, "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["forecast_horizon_hours"] == 6
    assert body["recommendations_only"] is True
    assert body["recommendations"][0]["driver_id"] == str(DRIVER_ID)
    assert body["recommendations"][0]["distance_type"] == "approximate_straight_line"
    assert body["recommendations"][0]["reason"]
    assert "driver_lat" not in response.text
    assert "driver_lng" not in response.text


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/intelligence/zones/surge", {"hours_ahead": 2}),
        (
            "/api/v1/intelligence/repositioning/recommendations",
            {"hours_ahead": 2},
        ),
        ("/api/v1/intelligence/zones/surge", {"limit": 0}),
        (
            "/api/v1/intelligence/repositioning/recommendations",
            {"limit": 201},
        ),
    ],
)
def test_advanced_intelligence_endpoints_validate_horizon_and_limit(
    client,
    path,
    params,
):
    api_client, _ = client

    response = api_client.get(path, params=params)

    assert response.status_code == 422
