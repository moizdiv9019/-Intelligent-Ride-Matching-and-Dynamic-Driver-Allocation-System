from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.v1.endpoints import offers
from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.services.dispatch_service import DispatchResult


DRIVER_ID = UUID("6f8e9d26-eef8-4ef9-b567-cbc5ab05d9e3")
OTHER_DRIVER_ID = UUID("7f8e9d26-eef8-4ef9-b567-cbc5ab05d9e4")
OFFER_ID = UUID("8f8e9d26-eef8-4ef9-b567-cbc5ab05d9e5")
RIDE_ID = UUID("a8493516-3fd4-4a14-9b0a-a7e5dfac01ab")
VEHICLE_ID = UUID("9f8e9d26-eef8-4ef9-b567-cbc5ab05d9e6")
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
_UNSET = object()


class MockResult:
    def __init__(self, *, row=None, mapping_row=_UNSET, rows=(), values=()):
        self.row = row
        self.mapping_row = row if mapping_row is _UNSET else mapping_row
        self.rows = list(rows)
        self.values = list(values)

    def mappings(self):
        return MockResult(
            row=self.mapping_row,
            rows=self.rows,
            values=self.values,
        )

    def first(self):
        return self.row

    def all(self):
        return self.values if self.values else self.rows

    def scalars(self):
        return self


class MockSession:
    def __init__(
        self,
        *,
        roles=("driver",),
        owner_id=DRIVER_ID,
        offer_status="offered",
        expired=False,
        active_ride=False,
        driver_exists=True,
        vehicle_exists=True,
        offers=(),
    ):
        self.roles = list(roles)
        self.owner_id = owner_id
        self.offer_status = offer_status
        self.expired = expired
        self.active_ride = active_ride
        self.driver_exists = driver_exists
        self.vehicle_exists = vehicle_exists
        self.offers = list(offers)
        self.execute_calls = []
        self.events = []
        self.commit_count = 0
        self.rollback_count = 0

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        normalized_sql = sql.lower()
        params = params or {}
        self.execute_calls.append((sql, params))

        if normalized_sql.startswith("select role from public.user_roles"):
            return MockResult(values=self.roles)
        if normalized_sql.startswith("select offer.id as offer_id"):
            return MockResult(rows=self.offers)
        if (
            normalized_sql.startswith("select id, ride_id from public.ride_offers")
            and "for update" not in normalized_sql
        ):
            if params.get("driver_user_id") != self.owner_id:
                return MockResult()
            return MockResult(row={"id": OFFER_ID, "ride_id": RIDE_ID})
        if normalized_sql.startswith(
            "select id, ride_id, driver_user_id, status"
        ):
            return MockResult(
                row={
                    "id": OFFER_ID,
                    "ride_id": RIDE_ID,
                    "driver_user_id": self.owner_id,
                    "status": self.offer_status,
                    "is_expired": self.expired,
                }
            )
        if normalized_sql.startswith("select id, status, assigned_driver_id"):
            return MockResult(
                row={
                    "id": RIDE_ID,
                    "status": "requested",
                    "assigned_driver_id": None,
                }
            )
        if normalized_sql.startswith("select verification_status, availability_status"):
            if not self.driver_exists:
                return MockResult()
            return MockResult(
                row={
                    "verification_status": "verified",
                    "availability_status": "available",
                }
            )
        if "id <> :ride_id" in normalized_sql and "assigned_driver_id" in normalized_sql:
            return MockResult(row={"id": UUID(int=1)} if self.active_ride else None)
        if normalized_sql.startswith("select id from public.vehicles"):
            if not self.vehicle_exists:
                return MockResult()
            return MockResult(
                row=(VEHICLE_ID,),
                mapping_row={"id": VEHICLE_ID},
            )
        if normalized_sql.startswith("update public.ride_offers"):
            if "set status = 'expired'" in normalized_sql:
                return MockResult(row={"id": OFFER_ID})
            return MockResult(row={"id": OFFER_ID})
        if normalized_sql.startswith("update public.rides"):
            return MockResult(row={"id": RIDE_ID})
        if normalized_sql.startswith("update public.driver_profiles"):
            return MockResult()
        if "insert into public.ride_events" in normalized_sql:
            self.events.append(params)
            return MockResult()
        raise AssertionError(f"Unexpected SQL statement: {sql}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def in_transaction(self):
        return self.commit_count == 0


def build_client(monkeypatch, db, driver_id=DRIVER_ID):
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: AuthenticatedUser(id=driver_id, email="driver@example.com"),
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app)


def test_incoming_offers_are_scoped_and_do_not_include_passenger_details(
    monkeypatch,
):
    db = MockSession(
        offers=[
            {
                "offer_id": OFFER_ID,
                "ride_id": RIDE_ID,
                "pickup_address": "Station",
                "destination_address": "Palace",
                "pickup_lat": 12.2958,
                "pickup_lng": 76.6394,
                "pickup_distance_m": 250,
                "pickup_eta_s": 60,
                "estimated_fare": 150.0,
                "expires_at": NOW,
            }
        ]
    )
    client = build_client(monkeypatch, db)

    response = client.get("/api/v1/drivers/me/offers?status=offered")

    assert response.status_code == 200
    assert response.json()[0]["pickup"] == {"lat": 12.296, "lng": 76.639}
    assert set(response.json()[0]) == {
        "offer_id",
        "ride_id",
        "pickup_address",
        "destination_address",
        "pickup",
        "pickup_distance_m",
        "pickup_eta_s",
        "estimated_fare",
        "expires_at",
    }
    query = next(
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("SELECT offer.id AS offer_id")
    )
    assert "offer.expires_at > now()" in query[0]
    assert query[1]["driver_user_id"] == DRIVER_ID


def test_driver_can_accept_owned_offer(monkeypatch):
    db = MockSession()
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/offers/{OFFER_ID}/accept")

    assert response.status_code == 200
    assert response.json() == {
        "offer_id": str(OFFER_ID),
        "ride_id": str(RIDE_ID),
        "status": "accepted",
        "dispatch_status": None,
    }
    sql_statements = [sql.lower() for sql, _ in db.execute_calls]
    offer_acceptance = next(
        sql.lower()
        for sql, _ in db.execute_calls
        if sql.lower().startswith("update public.ride_offers")
        and "set status = 'accepted'" in sql.lower()
    )
    assert "set status = 'accepted'" in offer_acceptance
    ride_assignment = next(
        (sql.lower(), params)
        for sql, params in db.execute_calls
        if sql.lower().startswith("update public.rides")
    )
    assert "assigned_driver_id = :driver_user_id" in ride_assignment[0]
    assert "assigned_vehicle_id = :vehicle_id" in ride_assignment[0]
    assert "status = 'accepted'" in ride_assignment[0]
    assert ride_assignment[1]["driver_user_id"] == DRIVER_ID
    assert ride_assignment[1]["vehicle_id"] == VEHICLE_ID
    assert any(
        sql.startswith("update public.driver_profiles")
        and "availability_status = 'busy'" in sql
        for sql in sql_statements
    )
    assert db.events[0]["event_type"] == "driver_offer_accepted"
    assert db.events[0]["ride_id"] == RIDE_ID
    assert db.events[0]["actor_user_id"] == DRIVER_ID
    assert db.events[0]["to_status"] == "accepted"
    assert db.commit_count == 1
    assert db.rollback_count == 0


def test_acceptance_returns_conflict_when_driver_profile_is_missing(monkeypatch):
    db = MockSession(driver_exists=False)
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/offers/{OFFER_ID}/accept")

    assert response.status_code == 409
    assert db.commit_count == 0
    assert db.rollback_count == 1
    assert not db.events


def test_acceptance_returns_conflict_when_active_vehicle_is_missing(monkeypatch):
    db = MockSession(vehicle_exists=False)
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/offers/{OFFER_ID}/accept")

    assert response.status_code == 409
    assert db.commit_count == 0
    assert db.rollback_count == 1
    assert not db.events


def test_offer_cannot_be_accepted_by_another_driver(monkeypatch):
    db = MockSession(owner_id=DRIVER_ID)
    client = build_client(monkeypatch, db, driver_id=OTHER_DRIVER_ID)

    response = client.post(f"/api/v1/driver/offers/{OFFER_ID}/accept")

    assert response.status_code == 404
    assert db.commit_count == 0
    assert not db.events


def test_expired_offer_cannot_be_accepted(monkeypatch):
    db = MockSession(expired=True)
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/offers/{OFFER_ID}/accept")

    assert response.status_code == 409
    assert db.events[0]["event_type"] == "driver_offer_expired"
    assert db.commit_count == 1
    assert not any(
        "assigned_driver_id = :driver_user_id" in sql
        for sql, _ in db.execute_calls
    )


def test_driver_with_another_active_ride_cannot_accept_offer(monkeypatch):
    db = MockSession(active_ride=True)
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/offers/{OFFER_ID}/accept")

    assert response.status_code == 409
    assert not any(
        "set status = 'accepted'" in sql.lower()
        for sql, _ in db.execute_calls
    )


def test_rejection_records_event_and_dispatches_next_driver(monkeypatch):
    db = MockSession()
    client = build_client(monkeypatch, db)
    dispatch_calls = []

    async def dispatch_next(session, ride_id):
        dispatch_calls.append((session, ride_id))
        return DispatchResult(status="offered")

    monkeypatch.setattr(offers, "dispatch_next_driver", dispatch_next)
    response = client.post(
        f"/api/v1/driver/offers/{OFFER_ID}/reject",
        json={"reason": "Too far away"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["dispatch_status"] == "offered"
    assert dispatch_calls == [(db, RIDE_ID)]
    assert any(
        event["event_type"] == "driver_offer_rejected"
        and event["metadata"].find("Too far away") >= 0
        for event in db.events
    )
    assert db.commit_count == 2


def test_rejecting_offer_requires_driver_role(monkeypatch):
    db = MockSession(roles=("passenger",))
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/offers/{OFFER_ID}/reject")

    assert response.status_code == 403
