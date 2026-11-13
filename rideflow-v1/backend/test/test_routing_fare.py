import asyncio

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints import rides
from app.core.config import settings
from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.schemas.location import Coordinate
from app.schemas.ride import GeoJSONLineString, RoutingResult
from app.services.fare_service import calculate_fare
from app.services.routing_service import get_driving_route


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
        self.execute_calls = []

    def execute(self, statement, params):
        sql = str(statement)
        self.execute_calls.append((sql, params))
        if "FROM public.user_roles" in sql:
            return MockResult(self.roles)
        raise AssertionError(f"Unexpected SQL statement: {sql}")


def build_client(monkeypatch, roles=("passenger",)):
    db = MockSession(roles)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: AuthenticatedUser(
            id="5b2b874a-9b9b-4dd1-8c76-83f173ea0843",
            email="passenger@example.com",
        ),
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app), db


def install_routing_client(monkeypatch, *, response=None, error=None, calls=None):
    class MockAsyncClient:
        def __init__(self, *, timeout):
            if calls is not None:
                calls["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

        async def get(self, url, *, params, headers):
            if calls is not None:
                calls.update(url=url, params=params, headers=headers)
            if error is not None:
                raise error
            return response

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)


def sample_routing_response():
    return httpx.Response(
        200,
        json={
            "code": "Ok",
            "routes": [
                {
                    "distance": 2500.5,
                    "duration": 300.25,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [76.6394, 12.2958],
                            [76.6500, 12.3100],
                        ],
                    },
                }
            ],
        },
    )


def test_routing_response_is_parsed_and_coordinates_use_longitude_first(
    monkeypatch,
):
    monkeypatch.setattr(settings, "routing_base_url", "https://router.example/")
    monkeypatch.setattr(settings, "routing_api_key", "test-routing-key")
    monkeypatch.setattr(settings, "routing_timeout_seconds", 7.5)
    calls = {}
    install_routing_client(
        monkeypatch,
        response=sample_routing_response(),
        calls=calls,
    )

    result = asyncio.run(
        get_driving_route(
            Coordinate(lat=12.2958, lng=76.6394),
            Coordinate(lat=12.3100, lng=76.6500),
        )
    )

    assert result.distance_m == 2500.5
    assert result.duration_s == 300.25
    assert result.geometry.coordinates == [
        (76.6394, 12.2958),
        (76.65, 12.31),
    ]
    assert calls["url"] == (
        "https://router.example/route/v1/driving/"
        "76.6394,12.2958;76.65,12.31"
    )
    assert calls["params"] == {"overview": "full", "geometries": "geojson"}
    assert calls["timeout"] == 7.5
    assert calls["headers"] == {"Authorization": "Bearer test-routing-key"}


@pytest.mark.parametrize(
    ("error", "response"),
    [
        (httpx.TimeoutException("timed out"), None),
        (None, httpx.Response(503)),
    ],
)
def test_routing_timeout_and_upstream_failure_return_503(
    monkeypatch, error, response
):
    monkeypatch.setattr(settings, "routing_base_url", "https://router.example")
    install_routing_client(monkeypatch, error=error, response=response)

    with pytest.raises(HTTPException) as exception_info:
        asyncio.run(
            get_driving_route(
                Coordinate(lat=12.0, lng=76.0),
                Coordinate(lat=12.1, lng=76.1),
            )
        )

    assert exception_info.value.status_code == 503
    assert "routing" in exception_info.value.detail.lower()


def test_routing_without_configuration_returns_503(monkeypatch):
    monkeypatch.setattr(settings, "routing_base_url", None)

    with pytest.raises(HTTPException) as exception_info:
        asyncio.run(
            get_driving_route(
                Coordinate(lat=12.0, lng=76.0),
                Coordinate(lat=12.1, lng=76.1),
            )
        )

    assert exception_info.value.status_code == 503


def test_fare_calculation_uses_configured_formula(monkeypatch):
    monkeypatch.setattr(settings, "fare_base_inr", 30)
    monkeypatch.setattr(settings, "fare_per_km_inr", 12)
    monkeypatch.setattr(settings, "fare_per_minute_inr", 1)
    monkeypatch.setattr(settings, "fare_minimum_inr", 40)

    fare = calculate_fare(distance_m=1234.5, duration_s=90)

    assert fare.base_fare == 30
    assert fare.distance_fare == 14.81
    assert fare.time_fare == 1.5
    assert fare.minimum_fare_adjustment == 0
    assert fare.estimated_total == 46.31
    assert fare.currency == "INR"


def test_fare_calculation_applies_minimum_fare(monkeypatch):
    monkeypatch.setattr(settings, "fare_base_inr", 30)
    monkeypatch.setattr(settings, "fare_per_km_inr", 12)
    monkeypatch.setattr(settings, "fare_per_minute_inr", 1)
    monkeypatch.setattr(settings, "fare_minimum_inr", 40)

    fare = calculate_fare(distance_m=0, duration_s=0)

    assert fare.minimum_fare_adjustment == 10
    assert fare.estimated_total == 40


def test_ride_estimate_returns_route_and_fare(monkeypatch):
    client, _ = build_client(monkeypatch)

    async def mock_route(pickup, destination):
        assert pickup.lat == 12.2958
        assert pickup.lng == 76.6394
        assert destination.lat == 12.31
        assert destination.lng == 76.65
        return RoutingResult(
            distance_m=2000,
            duration_s=300,
            geometry=GeoJSONLineString(
                type="LineString",
                coordinates=[[76.6394, 12.2958], [76.65, 12.31]],
            ),
        )

    monkeypatch.setattr(rides, "get_driving_route", mock_route)
    response = client.post(
        "/api/v1/rides/estimate",
        json={
            "pickup": {"lat": 12.2958, "lng": 76.6394},
            "destination": {"lat": 12.31, "lng": 76.65},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pickup"] == {"lat": 12.2958, "lng": 76.6394}
    assert payload["destination"] == {"lat": 12.31, "lng": 76.65}
    assert payload["estimated_distance_m"] == 2000
    assert payload["estimated_duration_s"] == 300
    assert payload["estimated_fare"] == 59
    assert payload["currency"] == "INR"
    assert payload["fare_breakdown"]["estimated_total"] == 59
    assert payload["route_geometry"]["type"] == "LineString"


def test_ride_estimate_rejects_invalid_coordinates(monkeypatch):
    client, _ = build_client(monkeypatch)
    monkeypatch.setattr(
        rides,
        "get_driving_route",
        lambda *_: pytest.fail("Routing must not run for invalid coordinates."),
    )

    response = client.post(
        "/api/v1/rides/estimate",
        json={
            "pickup": {"lat": 91, "lng": 76},
            "destination": {"lat": 12, "lng": 76},
        },
    )

    assert response.status_code == 422


def test_ride_estimate_rejects_frontend_route_and_fare(monkeypatch):
    client, _ = build_client(monkeypatch)

    response = client.post(
        "/api/v1/rides/estimate",
        json={
            "pickup": {"lat": 12, "lng": 76},
            "destination": {"lat": 13, "lng": 77},
            "estimated_distance_m": 1,
            "estimated_fare": 1,
        },
    )

    assert response.status_code == 422


def test_ride_estimate_requires_passenger_role(monkeypatch):
    client, _ = build_client(monkeypatch, roles=("driver",))
    monkeypatch.setattr(
        rides,
        "get_driving_route",
        lambda *_: pytest.fail("Routing must not run for a non-passenger."),
    )

    response = client.post(
        "/api/v1/rides/estimate",
        json={
            "pickup": {"lat": 12, "lng": 76},
            "destination": {"lat": 13, "lng": 77},
        },
    )

    assert response.status_code == 403
