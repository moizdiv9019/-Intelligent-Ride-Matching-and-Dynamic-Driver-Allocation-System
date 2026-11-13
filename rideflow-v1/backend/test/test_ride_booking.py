from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import rides
from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.services.dispatch_service import DispatchResult
from app.schemas.ride import GeoJSONLineString, RoutingResult


PASSENGER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
RIDE_ID = UUID("a8493516-3fd4-4a14-9b0a-a7e5dfac01ab")
CLIENT_REQUEST_ID = UUID("1cc8820a-6733-4f16-98f9-cfc11f913b38")
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


class MockResult:
    def __init__(self, *, row=None, rows=(), values=()):
        self.row = row
        self.rows = list(rows)
        self.values = list(values)

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return self.values if self.values else self.rows

    def scalars(self):
        return self


def ride_row(**overrides):
    row = {
        "ride_id": RIDE_ID,
        "status": "requested",
        "pickup_lat": 12.2958,
        "pickup_lng": 76.6394,
        "destination_lat": 12.31,
        "destination_lng": 76.65,
        "pickup_address": "Mysuru Railway Station",
        "destination_address": "Mysuru Palace",
        "estimated_distance_m": 2501,
        "estimated_duration_s": 300,
        "estimated_fare": 59.0,
        "final_distance_m": None,
        "final_fare": None,
        "currency": "INR",
        "fare_breakdown": {
            "base_fare": 30.0,
            "distance_fare": 30.0,
            "time_fare": 5.0,
            "minimum_fare_adjustment": 0.0,
            "estimated_total": 65.0,
            "currency": "INR",
        },
        "route_geometry": {
            "type": "LineString",
            "coordinates": [[76.6394, 12.2958], [76.65, 12.31]],
        },
        "assigned_driver_id": None,
        "vehicle_id": None,
        "vehicle_type": None,
        "requested_at": NOW,
        "accepted_at": None,
        "arrived_at": None,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "dispatch_status": "offered",
    }
    row.update(overrides)
    return row


class MockRideSession:
    def __init__(
        self,
        *,
        roles=("passenger",),
        existing_id=None,
        inserted=True,
        authorized=True,
        detail=None,
    ):
        self.roles = list(roles)
        self.existing_id = existing_id
        self.inserted = inserted
        self.authorized = authorized
        self.detail = detail or ride_row()
        self.execute_calls = []
        self.commit_count = 0
        self.rollback_count = 0
        self.idempotency_reads = 0

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        normalized_sql = sql.lower()
        params = params or {}
        self.execute_calls.append((sql, params))

        if "from public.user_roles" in normalized_sql:
            return MockResult(values=self.roles)
        if normalized_sql.startswith("select id from public.rides"):
            self.idempotency_reads += 1
            existing_id = self.existing_id
            if self.idempotency_reads > 1 and not self.inserted:
                existing_id = RIDE_ID
            return MockResult(row={"id": existing_id} if existing_id else None)
        if normalized_sql.startswith("insert into public.rides"):
            if not self.inserted:
                return MockResult()
            return MockResult(row={"id": RIDE_ID})
        if "insert into public.ride_events" in normalized_sql:
            return MockResult()
        if "from public.rides as ride" in normalized_sql:
            return MockResult(row=self.detail if self.authorized else None)
        raise AssertionError(f"Unexpected SQL statement: {sql}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def build_client(monkeypatch, db):
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: AuthenticatedUser(id=PASSENGER_ID, email="passenger@example.com"),
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app)


def booking_payload(**overrides):
    payload = {
        "client_request_id": str(CLIENT_REQUEST_ID),
        "pickup": {"lat": 12.2958, "lng": 76.6394},
        "destination": {"lat": 12.31, "lng": 76.65},
        "pickup_address": "Mysuru Railway Station",
        "destination_address": "Mysuru Palace",
    }
    payload.update(overrides)
    return payload


def fake_route():
    return RoutingResult(
        distance_m=2500.5,
        duration_s=300,
        geometry=GeoJSONLineString(
            type="LineString",
            coordinates=[[76.6394, 12.2958], [76.65, 12.31]],
        ),
    )


def test_successful_booking_calculates_route_and_dispatches_once(monkeypatch):
    db = MockRideSession()
    client = build_client(monkeypatch, db)
    route_calls = []
    dispatch_calls = []

    async def route_service(pickup, destination):
        route_calls.append((pickup, destination))
        return fake_route()

    async def dispatch_service(session, ride_id):
        dispatch_calls.append(ride_id)
        return DispatchResult(status="offered")

    monkeypatch.setattr(rides, "get_driving_route", route_service)
    monkeypatch.setattr(rides, "dispatch_next_driver", dispatch_service)

    response = client.post("/api/v1/rides", json=booking_payload())

    assert response.status_code == 201
    assert response.json()["ride_id"] == str(RIDE_ID)
    assert response.json()["dispatch_status"] == "offered"
    assert len(route_calls) == 1
    assert dispatch_calls == [RIDE_ID]
    insert_sql, params = next(
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("INSERT INTO public.rides")
    )
    assert "ON CONFLICT (passenger_id, client_request_id)" in insert_sql
    assert "WHERE client_request_id IS NOT NULL" in insert_sql
    assert "ST_MakePoint(:pickup_lng, :pickup_lat)" in insert_sql
    assert "ST_MakePoint(:destination_lng, :destination_lat)" in insert_sql
    assert "ST_GeomFromGeoJSON(:route_geometry)" in insert_sql
    assert params["passenger_id"] == PASSENGER_ID
    assert params["pickup_lng"] == 76.6394
    assert params["pickup_lat"] == 12.2958
    assert params["estimated_distance_m"] == 2500
    assert params["estimated_duration_s"] == 300
    assert "pooling_opt_in" in insert_sql
    assert params["pooling_opt_in"] is False
    assert db.commit_count == 1
    assert any(
        params.get("event_type") == "ride_requested"
        for _, params in db.execute_calls
    )


def test_ride_booking_accepts_pooling_opt_in_without_changing_individual_fare(
    monkeypatch,
):
    db = MockRideSession()
    client = build_client(monkeypatch, db)

    async def route_service(pickup, destination):
        return fake_route()

    async def dispatch_service(session, ride_id):
        return DispatchResult(status="offered")

    monkeypatch.setattr(rides, "get_driving_route", route_service)
    monkeypatch.setattr(rides, "dispatch_next_driver", dispatch_service)

    response = client.post(
        "/api/v1/rides",
        json=booking_payload(pooling_opt_in=True),
    )

    assert response.status_code == 201
    assert response.json()["estimated_fare"] == 59.0
    insert_sql, params = next(
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("INSERT INTO public.rides")
    )
    assert "pooling_opt_in" in insert_sql
    assert params["pooling_opt_in"] is True


def test_duplicate_idempotency_key_returns_existing_ride_without_dispatch(
    monkeypatch,
):
    db = MockRideSession(existing_id=RIDE_ID)
    client = build_client(monkeypatch, db)

    async def unexpected(*args):
        pytest.fail("Idempotent replay must not route or dispatch again.")

    monkeypatch.setattr(rides, "get_driving_route", unexpected)
    monkeypatch.setattr(rides, "dispatch_next_driver", unexpected)

    response = client.post("/api/v1/rides", json=booking_payload())

    assert response.status_code == 200
    assert response.json()["ride_id"] == str(RIDE_ID)
    assert not any(
        sql.startswith("INSERT INTO public.rides") for sql, _ in db.execute_calls
    )


def test_concurrent_idempotency_conflict_returns_existing_ride(monkeypatch):
    db = MockRideSession(inserted=False)
    client = build_client(monkeypatch, db)

    async def route_service(*args):
        return fake_route()

    async def unexpected(*args):
        pytest.fail("Conflicting insert must not create another dispatch.")

    monkeypatch.setattr(rides, "get_driving_route", route_service)
    monkeypatch.setattr(rides, "dispatch_next_driver", unexpected)

    response = client.post("/api/v1/rides", json=booking_payload())

    assert response.status_code == 200
    assert response.json()["ride_id"] == str(RIDE_ID)
    assert db.rollback_count >= 2


def test_invalid_coordinates_are_rejected(monkeypatch):
    client = build_client(monkeypatch, MockRideSession())
    monkeypatch.setattr(
        rides,
        "get_driving_route",
        lambda *_: pytest.fail("Invalid coordinates must not be routed."),
    )

    response = client.post(
        "/api/v1/rides",
        json=booking_payload(pickup={"lat": 91, "lng": 76}),
    )

    assert response.status_code == 422


def test_ride_creation_requires_passenger_role(monkeypatch):
    db = MockRideSession(roles=("driver",))
    client = build_client(monkeypatch, db)
    monkeypatch.setattr(
        rides,
        "get_driving_route",
        lambda *_: pytest.fail("Non-passenger must not be routed."),
    )

    response = client.post("/api/v1/rides", json=booking_payload())

    assert response.status_code == 403


def test_unrelated_user_cannot_retrieve_ride(monkeypatch):
    db = MockRideSession(roles=("passenger",), authorized=False)
    client = build_client(monkeypatch, db)

    response = client.get(f"/api/v1/rides/{RIDE_ID}")

    assert response.status_code == 404
    query = next(
        (sql, params)
        for sql, params in db.execute_calls
        if "FROM public.rides AS ride" in sql
    )
    assert "ride.passenger_id = :requester_id" in query[0]
    assert "ride.assigned_driver_id = :requester_id" in query[0]
    assert query[1]["requester_id"] == PASSENGER_ID


def test_completed_ride_detail_includes_final_values_and_stored_fare_breakdown(
    monkeypatch,
):
    completed_at = NOW
    final_breakdown = {
        "base_fare": 30.0,
        "distance_fare": 50.4,
        "time_fare": 5.5,
        "minimum_fare_adjustment": 0.0,
        "estimated_total": 85.9,
        "currency": "INR",
    }
    db = MockRideSession(
        detail=ride_row(
            status="completed",
            estimated_distance_m=2501,
            estimated_fare=59.0,
            final_distance_m=2501,
            final_fare=85.9,
            fare_breakdown=final_breakdown,
            accepted_at=NOW - timedelta(minutes=10),
            arrived_at=NOW - timedelta(minutes=8),
            started_at=NOW - timedelta(minutes=5),
            completed_at=completed_at,
        )
    )
    client = build_client(monkeypatch, db)

    response = client.get(f"/api/v1/rides/{RIDE_ID}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["estimated_distance_m"] == 2501
    assert detail["estimated_fare"] == 59.0
    assert detail["final_distance_m"] == 2501
    assert detail["final_fare"] == 85.9
    assert detail["fare_breakdown"] == final_breakdown
    assert detail["requested_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert detail["accepted_at"] is not None
    assert detail["arrived_at"] is not None
    assert detail["started_at"] is not None
    assert detail["completed_at"] == completed_at.isoformat().replace("+00:00", "Z")
    assert detail["cancelled_at"] is None

    query = next(
        sql for sql, _ in db.execute_calls if "FROM public.rides AS ride" in sql
    )
    assert "ride.final_distance_m" in query
    assert "ride.final_fare" in query


def test_non_completed_ride_detail_keeps_final_values_null(monkeypatch):
    db = MockRideSession(
        detail=ride_row(
            status="accepted",
            final_distance_m=None,
            final_fare=None,
            accepted_at=NOW,
        )
    )
    client = build_client(monkeypatch, db)

    response = client.get(f"/api/v1/rides/{RIDE_ID}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "accepted"
    assert detail["estimated_distance_m"] == 2501
    assert detail["estimated_fare"] == 59.0
    assert detail["final_distance_m"] is None
    assert detail["final_fare"] is None
    assert detail["fare_breakdown"]["estimated_total"] == 65.0
    assert detail["requested_at"] is not None
    assert detail["accepted_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert detail["arrived_at"] is None
    assert detail["started_at"] is None
    assert detail["completed_at"] is None
    assert detail["cancelled_at"] is None
