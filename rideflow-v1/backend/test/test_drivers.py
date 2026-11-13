from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app


USER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
OTHER_USER_ID = UUID("c6de2a96-253c-45f1-bdb2-a32682a9e3cf")
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def driver_profile(
    *,
    verification_status="verified",
    availability_status="offline",
):
    return {
        "user_id": USER_ID,
        "verification_status": verification_status,
        "availability_status": availability_status,
        "last_became_available_at": None,
        "last_seen_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def vehicle_row(**overrides):
    vehicle = {
        "id": UUID("6f8e9d26-eef8-4ef9-b567-cbc5ab05d9e3"),
        "driver_user_id": USER_ID,
        "vehicle_type": "car",
        "make_model": "RideFlow Sedan",
        "registration_number": "RF-123",
        "seat_capacity": 4,
        "wheelchair_accessible": False,
        "is_active": True,
        "created_at": NOW,
    }
    vehicle.update(overrides)
    return vehicle


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
    def __init__(
        self,
        *,
        profile_exists=True,
        roles=("driver",),
        driver=None,
        vehicle=None,
        duplicate_registration=False,
        active_ride=False,
    ):
        self.profile_exists = profile_exists
        self.roles = list(roles)
        self.driver = driver
        self.vehicle = vehicle
        self.duplicate_registration = duplicate_registration
        self.active_ride = active_ride
        self.execute_calls = []
        self.commit_count = 0
        self.rollback_count = 0

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        normalized_sql = sql.lower()
        params = params or {}
        self.execute_calls.append((sql, params))

        if "from public.user_roles" in normalized_sql and normalized_sql.startswith("select"):
            return MockResult(values=self.roles)
        if "select 1 from public.profiles" in normalized_sql:
            return MockResult(row={"exists": 1} if self.profile_exists else None)
        if normalized_sql.startswith("insert into public.driver_profiles"):
            if self.driver is not None:
                return MockResult()
            self.driver = driver_profile(
                verification_status="pending",
                availability_status="offline",
            )
            return MockResult(row=self.driver)
        if normalized_sql.startswith("insert into public.user_roles"):
            if "driver" not in self.roles:
                self.roles.append("driver")
            return MockResult()
        if normalized_sql.startswith("insert into public.vehicles"):
            if self.duplicate_registration:
                return MockResult()
            self.vehicle = vehicle_row(
                vehicle_type=params["vehicle_type"],
                make_model=params["make_model"],
                registration_number=params["registration_number"],
                seat_capacity=params["seat_capacity"],
                wheelchair_accessible=params["wheelchair_accessible"],
            )
            return MockResult(row=self.vehicle)
        if normalized_sql.startswith("select") and "from public.driver_profiles" in normalized_sql:
            return MockResult(row=self.driver)
        if normalized_sql.startswith("select") and "from public.vehicles" in normalized_sql:
            return MockResult(row=self.vehicle if self.vehicle and self.vehicle["is_active"] else None)
        if normalized_sql.startswith("select") and "from public.rides" in normalized_sql:
            return MockResult(row={"id": UUID(int=1)} if self.active_ride else None)
        if normalized_sql.startswith("update public.driver_profiles"):
            if "availability_status = :availability_status" in normalized_sql:
                self.driver["availability_status"] = params["availability_status"]
                if params["availability_status"] == "available":
                    self.driver["last_became_available_at"] = NOW
                self.driver["updated_at"] = NOW
                return MockResult(
                    row={
                        "user_id": self.driver["user_id"],
                        "availability_status": self.driver["availability_status"],
                        "last_became_available_at": self.driver[
                            "last_became_available_at"
                        ],
                    }
                )
            self.driver["last_seen_at"] = NOW
            self.driver["updated_at"] = NOW
            return MockResult()
        if normalized_sql.startswith("insert into public.driver_locations"):
            return MockResult(
                row={"driver_user_id": USER_ID, "received_at": NOW}
            )
        raise AssertionError(f"Unexpected SQL statement: {sql}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


@pytest.fixture
def db():
    return MockSession()


@pytest.fixture
def client(monkeypatch, db):
    current_user = AuthenticatedUser(id=USER_ID, email="driver@example.com")
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: current_user,
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app)


def onboarding_payload(**overrides):
    payload = {
        "vehicle_type": "car",
        "make_model": "RideFlow Sedan",
        "registration_number": "RF-123",
        "seat_capacity": 4,
        "wheelchair_accessible": False,
    }
    payload.update(overrides)
    return payload


def location_payload(**overrides):
    payload = {
        "lat": 12.2958,
        "lng": 76.6394,
        "accuracy_m": 10,
        "heading_deg": 90,
        "speed_mps": 5,
        "device_recorded_at": "2026-11-13T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_driver_onboarding_creates_role_profile_and_vehicle(client, db):
    response = client.post(
        "/api/v1/drivers/me/onboarding",
        json=onboarding_payload(),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["driver_profile"]["user_id"] == str(USER_ID)
    assert result["driver_profile"]["verification_status"] == "pending"
    assert result["driver_profile"]["availability_status"] == "offline"
    assert result["vehicle"]["is_active"] is True
    assert db.roles == ["driver"]
    assert db.commit_count == 1

    driver_insert = next(
        sql for sql, _ in db.execute_calls
        if sql.startswith("INSERT INTO public.driver_profiles")
    )
    role_insert, role_params = next(
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("INSERT INTO public.user_roles")
    )
    assert "'pending', 'offline'" in driver_insert
    assert "VALUES (:user_id, 'driver')" in role_insert
    assert role_params == {"user_id": USER_ID}


def test_onboarding_requires_an_existing_user_profile(client, db):
    db.profile_exists = False

    response = client.post(
        "/api/v1/drivers/me/onboarding",
        json=onboarding_payload(),
    )

    assert response.status_code == 409
    assert "profile" in response.json()["detail"].lower()
    assert db.commit_count == 0
    assert db.rollback_count == 1


def test_duplicate_onboarding_returns_409(client, db):
    db.driver = driver_profile(verification_status="pending")

    response = client.post(
        "/api/v1/drivers/me/onboarding",
        json=onboarding_payload(),
    )

    assert response.status_code == 409
    assert "already" in response.json()["detail"]
    assert db.commit_count == 0
    assert db.rollback_count == 1


def test_duplicate_vehicle_registration_rolls_back_onboarding(client, db):
    db.duplicate_registration = True

    response = client.post(
        "/api/v1/drivers/me/onboarding",
        json=onboarding_payload(),
    )

    assert response.status_code == 409
    assert "registration number" in response.json()["detail"]
    assert db.commit_count == 0
    assert db.rollback_count == 1


def test_driver_can_retrieve_only_their_own_data(client, db):
    db.driver = driver_profile()
    db.vehicle = vehicle_row()

    response = client.get(
        "/api/v1/drivers/me",
        params={"driver_user_id": str(OTHER_USER_ID)},
    )

    assert response.status_code == 200
    assert response.json()["driver_profile"]["user_id"] == str(USER_ID)
    assert response.json()["active_vehicle"]["driver_user_id"] == str(USER_ID)
    own_driver_query = next(
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("SELECT user_id, verification_status")
    )
    assert own_driver_query[1] == {"user_id": USER_ID}


def test_non_driver_cannot_access_driver_profile(client, db):
    db.roles = ["passenger"]

    response = client.get("/api/v1/drivers/me")

    assert response.status_code == 403
    assert db.execute_calls[0][1] == {"user_id": USER_ID}
    assert len(db.execute_calls) == 1


def test_pending_driver_cannot_become_available(client, db):
    db.driver = driver_profile(verification_status="pending")

    response = client.patch(
        "/api/v1/drivers/me/availability",
        json={"availability_status": "available"},
    )

    assert response.status_code == 409
    assert db.commit_count == 0
    assert db.rollback_count == 1


def test_verified_driver_can_update_availability(client, db):
    db.driver = driver_profile(verification_status="verified")

    response = client.patch(
        "/api/v1/drivers/me/availability",
        json={"availability_status": "available"},
    )

    assert response.status_code == 200
    assert response.json()["availability_status"] == "available"
    assert response.json()["last_became_available_at"] is not None
    assert db.commit_count == 1


def test_busy_status_cannot_be_set_manually(client, db):
    response = client.patch(
        "/api/v1/drivers/me/availability",
        json={"availability_status": "busy"},
    )

    assert response.status_code == 422
    assert db.commit_count == 0


def test_driver_cannot_go_offline_with_an_active_ride(client, db):
    db.driver = driver_profile(availability_status="busy")
    db.active_ride = True

    response = client.patch(
        "/api/v1/drivers/me/availability",
        json={"availability_status": "offline"},
    )

    assert response.status_code == 409
    assert db.commit_count == 0
    assert db.rollback_count == 1


def test_unverified_driver_cannot_submit_location(client, db):
    db.driver = driver_profile(
        verification_status="pending",
        availability_status="available",
    )

    response = client.post(
        "/api/v1/drivers/me/location",
        json=location_payload(),
    )

    assert response.status_code == 409
    assert db.commit_count == 0
    assert db.rollback_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lat", 90.01),
        ("lat", -90.01),
        ("lng", 180.01),
        ("lng", -180.01),
        ("accuracy_m", -1),
        ("speed_mps", -1),
        ("heading_deg", 360),
    ],
)
def test_location_rejects_invalid_coordinates_and_measurements(
    client, db, field, value
):
    response = client.post(
        "/api/v1/drivers/me/location",
        json=location_payload(**{field: value}),
    )

    assert response.status_code == 422


def test_location_upsert_uses_longitude_then_latitude(client, db):
    db.driver = driver_profile(
        verification_status="verified",
        availability_status="available",
    )

    response = client.post(
        "/api/v1/drivers/me/location",
        json=location_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "driver_user_id": str(USER_ID),
        "received_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    location_insert, params = next(
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("INSERT INTO public.driver_locations")
    )
    assert "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography" in location_insert
    assert "ON CONFLICT (driver_user_id) DO UPDATE" in location_insert
    assert params["user_id"] == USER_ID
    assert params["lng"] == 76.6394
    assert params["lat"] == 12.2958


def test_onboarding_rejects_frontend_identity_and_status_fields(client, db):
    response = client.post(
        "/api/v1/drivers/me/onboarding",
        json=onboarding_payload(
            user_id=str(OTHER_USER_ID),
            verification_status="verified",
            availability_status="available",
            role="admin",
        ),
    )

    assert response.status_code == 422
    assert db.execute_calls == []


def test_location_updates_last_seen_timestamp(client, db):
    db.driver = driver_profile(
        verification_status="verified",
        availability_status="busy",
    )

    response = client.post(
        "/api/v1/drivers/me/location",
        json=location_payload(),
    )

    assert response.status_code == 200
    assert any(
        sql.startswith("UPDATE public.driver_profiles")
        and "last_seen_at = now()" in sql
        for sql, _ in db.execute_calls
    )
