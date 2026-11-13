from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.schemas.location import Coordinate
from app.schemas.ride import (
    GeoJSONLineString,
    PassengerPoolEstimate,
    PoolingFareComparison,
    PoolingPreviewResponse,
    PoolingStop,
)


PASSENGER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
RIDE_ID = UUID("a8493516-3fd4-4a14-9b0a-a7e5dfac01ab")


class MockResult:
    def __init__(self, values=()):
        self.values = list(values)

    def scalars(self):
        return self

    def all(self):
        return self.values


class MockSession:
    def __init__(self, roles=("passenger",)):
        self.roles = list(roles)
        self.statements = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if "FROM public.user_roles" in sql:
            return MockResult(self.roles)
        raise AssertionError(f"Pooling endpoint should not execute ride queries: {sql}")


@pytest.fixture
def api_client(monkeypatch):
    db = MockSession()
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: AuthenticatedUser(id=PASSENGER_ID, email="passenger@example.com"),
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    yield TestClient(app), db
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def preview_response():
    return PoolingPreviewResponse(
        pool_available=True,
        explanation="Preview only; no rides were changed.",
        optimized_stops=[
            PoolingStop(
                passenger_label="A",
                stop_type="pickup",
                coordinate=Coordinate(lat=12.0, lng=76.0),
            ),
            PoolingStop(
                passenger_label="B",
                stop_type="pickup",
                coordinate=Coordinate(lat=12.005, lng=76.005),
            ),
            PoolingStop(
                passenger_label="A",
                stop_type="dropoff",
                coordinate=Coordinate(lat=12.1, lng=76.1),
            ),
            PoolingStop(
                passenger_label="B",
                stop_type="dropoff",
                coordinate=Coordinate(lat=12.105, lng=76.105),
            ),
        ],
        route_geometry=GeoJSONLineString(
            type="LineString",
            coordinates=[[76.0, 12.0], [76.1, 12.1]],
        ),
        pooled_distance_m=13_000,
        pooled_duration_s=660,
        fare_comparison=PoolingFareComparison(
            discount_percent=10,
            total_separate_fare_estimate=320,
            pooled_base_fare_estimate=197,
            pooled_total_fare_estimate=177.3,
            estimated_total_passenger_savings=142.7,
            passenger_fares=[
                PassengerPoolEstimate(
                    passenger_label="A",
                    individual_distance_m=10_000,
                    individual_duration_s=600,
                    individual_fare_estimate=160,
                    pooled_distance_m=11_000,
                    pooled_duration_s=600,
                    detour_distance_m=1000,
                    detour_percent=10,
                    additional_duration_s=0,
                    pooled_fare_estimate=88.65,
                ),
                PassengerPoolEstimate(
                    passenger_label="B",
                    individual_distance_m=10_000,
                    individual_duration_s=600,
                    individual_fare_estimate=160,
                    pooled_distance_m=2000,
                    pooled_duration_s=60,
                    detour_distance_m=0,
                    detour_percent=0,
                    additional_duration_s=0,
                    pooled_fare_estimate=88.65,
                ),
            ],
        ),
    )


def test_passenger_can_request_preview_and_only_aggregate_route_data_is_returned(
    api_client, monkeypatch
):
    from app.api.v1.endpoints import pooling

    client, db = api_client
    calls = []

    async def fake_preview_pool(session, *, ride_id, passenger_id):
        calls.append((session, ride_id, passenger_id))
        return preview_response()

    monkeypatch.setattr(pooling, "preview_pool", fake_preview_pool)

    response = client.post(f"/api/v1/rides/{RIDE_ID}/pooling/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["pool_available"] is True
    assert body["pooled_distance_m"] == 13_000
    assert body["pooled_duration_s"] == 660
    assert body["route_geometry"]["type"] == "LineString"
    assert body["fare_comparison"]["estimates_only"] is True
    assert [stop["passenger_label"] for stop in body["optimized_stops"]] == [
        "A",
        "B",
        "A",
        "B",
    ]
    assert "passenger_id" not in body
    assert "ride_id" not in body
    assert "driver_id" not in body
    assert calls == [(db, RIDE_ID, PASSENGER_ID)]
    assert len(db.statements) == 1


def test_non_passenger_cannot_access_pooling_preview(api_client, monkeypatch):
    from app.api.v1.endpoints import pooling

    client, db = api_client
    db.roles = ["driver"]
    monkeypatch.setattr(
        pooling,
        "preview_pool",
        lambda *_args, **_kwargs: pytest.fail(
            "The service must not run when role authorization fails."
        ),
    )

    response = client.post(f"/api/v1/rides/{RIDE_ID}/pooling/preview")

    assert response.status_code == 403
    assert len(db.statements) == 1
