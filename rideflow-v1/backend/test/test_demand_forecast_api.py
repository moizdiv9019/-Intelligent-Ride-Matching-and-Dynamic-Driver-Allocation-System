from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app


USER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")


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
        if "FROM public.user_roles" in sql:
            return MockResult(self.roles)
        raise AssertionError(f"Forecasting must not query operational tables: {sql}")


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
        "/api/v1/intelligence/zones/forecast",
        "/api/v1/intelligence/zones/forecast/evaluation",
    ],
)
def test_passenger_cannot_access_forecasting_endpoints(client, path):
    api_client, db = client
    db.roles = ["passenger"]

    response = api_client.get(path)

    assert response.status_code == 403
    assert len(db.statements) == 1


@pytest.mark.parametrize("hours_ahead", [1, 3, 6])
def test_admin_forecast_response_shape_and_synthetic_isolation(client, hours_ahead):
    api_client, db = client

    response = api_client.get(
        "/api/v1/intelligence/zones/forecast",
        params={"hours_ahead": hours_ahead, "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["forecast_horizon_hours"] == hours_ahead
    assert body["model_name"] == "GradientBoostingRegressor"
    assert body["data_source"] == "synthetic_simulation"
    assert len(body["zones"]) >= 8
    assert all(
        zone["predicted_hourly_demand"] >= 0
        and "zone_id" in zone
        and "center_lat" in zone
        and "center_lng" in zone
        and "forecast_timestamp" in zone
        for zone in body["zones"]
    )
    assert all(statement.lower().startswith("select") for statement in db.statements)
    assert len(db.statements) == 1


def test_admin_evaluation_is_chronological_and_reports_errors(client):
    api_client, db = client

    response = api_client.get(
        "/api/v1/intelligence/zones/forecast/evaluation",
    )

    assert response.status_code == 200
    body = response.json()
    results = body["results"]
    assert body["model_name"] == "GradientBoostingRegressor"
    assert body["data_source"] == "synthetic_simulation"
    assert "not_real_world_accuracy" in body["evaluation_label"]
    assert body["training_dataset_size"] > 0
    assert body["mae"] >= 0
    assert body["rmse"] >= 0
    assert len(results) > 0
    timestamps = [point["timestamp"] for point in results]
    assert timestamps == sorted(timestamps)
    assert all(
        point["absolute_error"]
        == abs(point["actual_demand"] - point["predicted_demand"])
        for point in results
    )
    assert all(statement.lower().startswith("select") for statement in db.statements)
    assert len(db.statements) == 1


def test_invalid_horizon_is_rejected(client):
    api_client, _ = client

    response = api_client.get(
        "/api/v1/intelligence/zones/forecast",
        params={"hours_ahead": 2},
    )

    assert response.status_code == 422


def test_forecasting_returns_service_unavailable_for_insufficient_data(
    client, monkeypatch
):
    from app.api.v1.endpoints import zone_intelligence
    from app.services.demand_forecast_service import InsufficientForecastDataError

    api_client, _ = client

    def fail_for_short_history(**kwargs):
        raise InsufficientForecastDataError("Synthetic history is too short.")

    monkeypatch.setattr(
        zone_intelligence,
        "generate_demand_forecast",
        fail_for_short_history,
    )

    response = api_client.get("/api/v1/intelligence/zones/forecast")

    assert response.status_code == 503
    assert "too short" in response.json()["detail"]
