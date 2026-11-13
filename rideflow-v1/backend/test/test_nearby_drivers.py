from uuid import UUID

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app


USER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
DRIVER_ID = UUID("c6de2a96-253c-45f1-bdb2-a32682a9e3cf")


class MockResult:
    def __init__(self, *, rows=(), values=()):
        self.rows = list(rows)
        self.values = list(values)

    def mappings(self):
        return self

    def all(self):
        return self.values if self.values else self.rows

    def scalars(self):
        return self


class MockSession:
    def __init__(self, roles=("passenger",), rows=()):
        self.roles = list(roles)
        self.rows = list(rows)
        self.execute_calls = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        self.execute_calls.append((sql, params))
        if "FROM public.user_roles" in sql:
            return MockResult(values=self.roles)
        if "ST_DWithin" in sql:
            return MockResult(rows=self.rows)
        raise AssertionError(f"Unexpected SQL statement: {sql}")


def build_client(monkeypatch, db):
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: AuthenticatedUser(id=USER_ID, email="passenger@example.com"),
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app)


def test_nearby_query_filters_driver_state_vehicle_and_fresh_gps(
    monkeypatch,
):
    db = MockSession()
    client = build_client(monkeypatch, db)

    response = client.get(
        "/api/v1/drivers/nearby",
        params={"lat": 12.2958, "lng": 76.6394, "radius_m": 2500},
    )

    assert response.status_code == 200
    sql, params = next(
        (sql, params) for sql, params in db.execute_calls if "ST_DWithin" in sql
    )
    assert "dp.verification_status = 'verified'" in sql
    assert "dp.availability_status = 'available'" in sql
    assert "dl.device_recorded_at >= now()" in sql
    assert "dl.received_at >= now()" in sql
    assert "is_active = TRUE" in sql
    assert "ORDER BY distance_m ASC" in sql
    assert "LIMIT :result_limit" in sql
    assert "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography" in sql
    assert params["max_age_seconds"] == settings.driver_location_max_age_seconds
    assert params["radius_m"] == 2500
    assert params["result_limit"] == 50


def test_nearby_results_round_coordinates_and_only_return_map_fields(monkeypatch):
    db = MockSession(
        rows=[
            {
                "driver_id": DRIVER_ID,
                "lat": 12.2958,
                "lng": 76.6394,
                "vehicle_type": "car",
                "distance_m": 250.5,
            }
        ]
    )
    client = build_client(monkeypatch, db)

    response = client.get(
        "/api/v1/drivers/nearby",
        params={"lat": 12.2958, "lng": 76.6394},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "driver_id": str(DRIVER_ID),
            "lat": 12.296,
            "lng": 76.639,
            "vehicle_type": "car",
            "distance_m": 250.5,
        }
    ]


def test_nearby_radius_must_be_within_allowed_range(monkeypatch):
    client = build_client(monkeypatch, MockSession())

    response = client.get(
        "/api/v1/drivers/nearby",
        params={"lat": 12.0, "lng": 76.0, "radius_m": 0},
    )

    assert response.status_code == 422


def test_nearby_coordinates_must_be_valid(monkeypatch):
    client = build_client(monkeypatch, MockSession())

    response = client.get(
        "/api/v1/drivers/nearby",
        params={"lat": 91, "lng": 76},
    )

    assert response.status_code == 422


def test_nearby_requires_passenger_role(monkeypatch):
    db = MockSession(roles=("driver",))
    client = build_client(monkeypatch, db)

    response = client.get(
        "/api/v1/drivers/nearby",
        params={"lat": 12.0, "lng": 76.0},
    )

    assert response.status_code == 403
    assert not any("ST_DWithin" in sql for sql, _ in db.execute_calls)
