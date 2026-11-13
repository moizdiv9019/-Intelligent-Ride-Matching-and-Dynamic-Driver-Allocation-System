from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import ride_lifecycle
from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.schemas.ride import FareBreakdown
from app.services.dispatch_service import DispatchResult


PASSENGER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
OTHER_PASSENGER_ID = UUID("c6de2a96-253c-45f1-bdb2-a32682a9e3cf")
DRIVER_ID = UUID("6f8e9d26-eef8-4ef9-b567-cbc5ab05d9e3")
OTHER_DRIVER_ID = UUID("7f8e9d26-eef8-4ef9-b567-cbc5ab05d9e4")
VEHICLE_ID = UUID("9f8e9d26-eef8-4ef9-b567-cbc5ab05d9e6")
RIDE_ID = UUID("a8493516-3fd4-4a14-9b0a-a7e5dfac01ab")
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

    def one(self):
        return self.row

    def all(self):
        return self.values if self.values else self.rows

    def scalars(self):
        return self


def ride_state(**overrides):
    ride = {
        "id": RIDE_ID,
        "passenger_id": PASSENGER_ID,
        "assigned_driver_id": DRIVER_ID,
        "assigned_vehicle_id": VEHICLE_ID,
        "status": "accepted",
        "estimated_distance_m": 4200,
        "started_at": NOW - timedelta(minutes=5),
        "arrived_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "final_distance_m": None,
        "final_fare": None,
        "fare_breakdown": None,
        "pickup_address": "Station",
        "destination_address": "Palace",
        "estimated_fare": 150.0,
        "created_at": NOW - timedelta(hours=1),
        "requested_at": NOW - timedelta(hours=1),
        "accepted_at": NOW - timedelta(minutes=10),
    }
    ride.update(overrides)
    return ride


class LifecycleSession:
    def __init__(
        self,
        *,
        ride=None,
        roles=("driver",),
        driver_verified=True,
        location_fresh=True,
        completed_at=NOW,
        history=(),
        events=(),
        event_access=True,
    ):
        self.ride = ride_state() if ride is None else ride_state(**ride)
        self.roles = list(roles)
        self.driver_verified = driver_verified
        self.location_fresh = location_fresh
        self.driver_availability = "busy"
        self.completed_at = completed_at
        self.history = list(history)
        self.events = list(events)
        self.event_access = event_access
        self.execute_calls = []
        self.recorded_events = []
        self.commit_count = 0
        self.rollback_count = 0

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        normalized_sql = sql.lower()
        params = params or {}
        self.execute_calls.append((sql, params))

        if normalized_sql.startswith("select role from public.user_roles"):
            return MockResult(values=self.roles)
        if normalized_sql.startswith("select 1 from public.user_roles"):
            return MockResult(row={"exists": 1} if params["role"] in self.roles else None)
        if normalized_sql.startswith("select id, passenger_id, assigned_driver_id"):
            return MockResult(
                row=self.ride.copy() if self.ride["id"] == params["ride_id"] else None
            )
        if normalized_sql.startswith("select clock_timestamp()"):
            return MockResult(row={"completed_at": self.completed_at})
        if "update public.driver_profiles as driver" in normalized_sql:
            self.driver_availability = (
                "available"
                if self.driver_verified and self.location_fresh
                else "offline"
            )
            return MockResult(
                row={"availability_status": self.driver_availability}
            )
        if normalized_sql.startswith("update public.rides"):
            if "set status = 'arriving'" in normalized_sql:
                if self.ride["status"] != "accepted":
                    return MockResult()
                self.ride["status"] = "arriving"
                self.ride["arrived_at"] = self.completed_at
                return MockResult(row={"arrived_at": self.completed_at})
            if "set status = 'ongoing'" in normalized_sql:
                if self.ride["status"] != "arriving":
                    return MockResult()
                self.ride["status"] = "ongoing"
                self.ride["started_at"] = self.completed_at
                return MockResult(row={"started_at": self.completed_at})
            if "set status = 'completed'" in normalized_sql:
                if self.ride["status"] != "ongoing":
                    return MockResult()
                self.ride.update(
                    status="completed",
                    final_distance_m=params["final_distance_m"],
                    final_fare=params["final_fare"],
                    completed_at=params["completed_at"],
                )
                import json

                self.ride["fare_breakdown"] = json.loads(params["fare_breakdown"])
                return MockResult(
                    row={
                        "final_distance_m": self.ride["final_distance_m"],
                        "final_fare": self.ride["final_fare"],
                        "fare_breakdown": self.ride["fare_breakdown"],
                        "completed_at": self.ride["completed_at"],
                    }
                )
            if "set status = 'cancelled'" in normalized_sql:
                if self.ride["status"] != params["previous_status"]:
                    return MockResult()
                self.ride.update(
                    status="cancelled",
                    cancelled_at=self.completed_at,
                    cancelled_by=params["passenger_id"],
                    cancellation_reason=params["reason"],
                )
                return MockResult(row={"cancelled_at": self.completed_at})
            if "set status = 'requested'" in normalized_sql:
                if self.ride["status"] != params["previous_status"]:
                    return MockResult()
                self.ride.update(
                    status="requested",
                    assigned_driver_id=None,
                    assigned_vehicle_id=None,
                )
                return MockResult(row={"id": RIDE_ID})
        if normalized_sql.startswith("update public.ride_offers"):
            return MockResult()
        if normalized_sql.startswith("insert into public.ride_events"):
            self.recorded_events.append(params)
            return MockResult()
        if normalized_sql.startswith("select id as ride_id"):
            rows = sorted(
                self.history,
                key=lambda row: (row["created_at"], row["ride_id"]),
                reverse=True,
            )
            if "cursor_created_at" in params:
                cursor_key = (params["cursor_created_at"], params["cursor_ride_id"])
                rows = [
                    row
                    for row in rows
                    if (row["created_at"], row["ride_id"]) < cursor_key
                ]
            return MockResult(rows=rows[: params["fetch_limit"]])
        if normalized_sql.startswith("select id from public.rides"):
            return MockResult(row={"id": RIDE_ID} if self.event_access else None)
        if normalized_sql.startswith("select id as event_id"):
            rows = sorted(
                self.events,
                key=lambda row: (row["created_at"], row["event_id"]),
            )
            return MockResult(rows=rows)
        raise AssertionError(f"Unexpected SQL statement: {sql}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def build_client(monkeypatch, db, user_id=DRIVER_ID, roles=("driver",)):
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: AuthenticatedUser(id=user_id, email="user@example.com"),
    )
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app)


def history_row(ride_id, created_at):
    return {
        "ride_id": ride_id,
        "status": "completed",
        "pickup_address": "Station",
        "destination_address": "Palace",
        "estimated_fare": 100.0,
        "final_fare": 125.0,
        "created_at": created_at,
        "requested_at": created_at,
        "accepted_at": created_at + timedelta(minutes=1),
        "arrived_at": created_at + timedelta(minutes=2),
        "started_at": created_at + timedelta(minutes=3),
        "completed_at": created_at + timedelta(minutes=8),
        "cancelled_at": None,
    }


def test_assigned_driver_can_mark_ride_arrived(monkeypatch):
    db = LifecycleSession(ride=ride_state(status="accepted"))
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/arrived")

    assert response.status_code == 200
    assert response.json()["status"] == "arriving"
    assert db.ride["arrived_at"] == NOW
    assert db.recorded_events[0]["event_type"] == "driver_arrived"
    lock_query = next(sql for sql, _ in db.execute_calls if "FOR UPDATE" in sql)
    assert "FOR UPDATE" in lock_query
    transition_sql = next(
        sql
        for sql, _ in db.execute_calls
        if "set status = 'arriving'" in sql.lower()
    )
    assert "AND status = 'accepted'" in transition_sql
    assert db.commit_count == 1


def test_unassigned_driver_cannot_mark_ride_arrived(monkeypatch):
    db = LifecycleSession(ride=ride_state(assigned_driver_id=None))
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/arrived")

    assert response.status_code == 404
    assert db.ride["status"] == "accepted"
    assert db.rollback_count == 1


def test_arriving_ride_can_start(monkeypatch):
    db = LifecycleSession(ride=ride_state(status="arriving"))
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/start")

    assert response.status_code == 200
    assert response.json()["status"] == "ongoing"
    assert db.recorded_events[0]["event_type"] == "ride_started"


def test_ride_cannot_be_started_twice(monkeypatch):
    db = LifecycleSession(ride=ride_state(status="ongoing"))
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/start")

    assert response.status_code == 409
    assert db.ride["status"] == "ongoing"
    assert not db.recorded_events


def test_ongoing_ride_completion_uses_server_duration_and_estimated_distance(
    monkeypatch,
):
    started_at = NOW - timedelta(minutes=5, seconds=30)
    db = LifecycleSession(
        ride=ride_state(status="ongoing", started_at=started_at),
        completed_at=NOW,
    )
    client = build_client(monkeypatch, db)
    fare_calls = []
    fare = FareBreakdown(
        base_fare=30,
        distance_fare=50.4,
        time_fare=5.5,
        minimum_fare_adjustment=0,
        estimated_total=85.9,
        currency="INR",
    )

    def calculate(distance_m, duration_s):
        fare_calls.append((distance_m, duration_s))
        return fare

    monkeypatch.setattr(ride_lifecycle, "calculate_fare", calculate)

    response = client.post(
        f"/api/v1/driver/rides/{RIDE_ID}/complete",
        json={"distance_m": 999999, "duration_s": 1, "fare": 1},
    )

    assert response.status_code == 200
    assert response.json()["final_distance_m"] == 4200
    assert response.json()["final_fare"] == 85.9
    assert fare_calls == [(4200, 330.0)]
    assert db.ride["status"] == "completed"
    assert db.ride["completed_at"] == NOW
    assert db.ride["final_fare"] == 85.9
    assert db.ride["fare_breakdown"]["estimated_total"] == 85.9
    assert db.recorded_events[0]["event_type"] == "ride_completed"
    assert db.recorded_events[0]["metadata"]
    assert db.driver_availability == "available"
    assert db.commit_count == 1
    update_sql = next(
        sql
        for sql, _ in db.execute_calls
        if "set status = 'completed'" in sql.lower()
    )
    assert "final_distance_m = :final_distance_m" in update_sql
    assert "final_fare = :final_fare" in update_sql


def test_driver_becomes_available_after_completion_only_with_fresh_location(
    monkeypatch,
):
    db = LifecycleSession(
        ride=ride_state(status="ongoing"),
        location_fresh=True,
    )
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/complete")

    assert response.status_code == 200
    assert db.driver_availability == "available"


def test_driver_with_stale_location_is_released_offline(monkeypatch):
    db = LifecycleSession(
        ride=ride_state(status="ongoing"),
        location_fresh=False,
    )
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/complete")

    assert response.status_code == 200
    assert db.driver_availability == "offline"


def test_unverified_driver_is_released_offline(monkeypatch):
    db = LifecycleSession(
        ride=ride_state(status="ongoing"),
        driver_verified=False,
        location_fresh=True,
    )
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/complete")

    assert response.status_code == 200
    assert db.driver_availability == "offline"


def test_passenger_can_cancel_requested_ride(monkeypatch):
    db = LifecycleSession(
        ride=ride_state(status="requested", assigned_driver_id=None, assigned_vehicle_id=None),
        roles=("passenger",),
    )
    client = build_client(monkeypatch, db, user_id=PASSENGER_ID, roles=("passenger",))

    response = client.post(
        f"/api/v1/rides/{RIDE_ID}/cancel",
        json={"reason": "Changed plans"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert db.ride["cancelled_by"] == PASSENGER_ID
    assert db.ride["cancellation_reason"] == "Changed plans"
    assert db.recorded_events[0]["event_type"] == "ride_cancelled_by_passenger"
    assert db.commit_count == 1


@pytest.mark.parametrize("ride_status", ["accepted", "arriving"])
def test_passenger_can_cancel_assigned_ride_and_release_driver(
    monkeypatch, ride_status
):
    db = LifecycleSession(ride=ride_state(status=ride_status), roles=("passenger",))
    client = build_client(monkeypatch, db, user_id=PASSENGER_ID, roles=("passenger",))

    response = client.post(f"/api/v1/rides/{RIDE_ID}/cancel")

    assert response.status_code == 200
    assert db.ride["status"] == "cancelled"
    assert any(
        "update public.driver_profiles as driver" in sql.lower()
        for sql, _ in db.execute_calls
    )
    assert db.driver_availability == "available"
    assert any(
        "update public.ride_offers" in sql.lower()
        and "status = 'withdrawn'" in sql.lower()
        for sql, _ in db.execute_calls
    )


def test_passenger_cannot_cancel_another_passengers_ride(monkeypatch):
    db = LifecycleSession(
        ride=ride_state(passenger_id=OTHER_PASSENGER_ID, status="requested"),
        roles=("passenger",),
    )
    client = build_client(monkeypatch, db, user_id=PASSENGER_ID, roles=("passenger",))

    response = client.post(f"/api/v1/rides/{RIDE_ID}/cancel")

    assert response.status_code == 404
    assert db.ride["status"] == "requested"
    assert not db.recorded_events


@pytest.mark.parametrize("ride_status", ["accepted", "arriving"])
def test_driver_can_cancel_before_journey_start_and_redispatch(
    monkeypatch, ride_status
):
    db = LifecycleSession(ride=ride_state(status=ride_status))
    client = build_client(monkeypatch, db)
    dispatch_calls = []

    async def dispatch(session, ride_id):
        dispatch_calls.append((session, ride_id, session.commit_count))
        return DispatchResult(
            status="offered",
            driver_user_id=OTHER_DRIVER_ID,
        )

    monkeypatch.setattr(ride_lifecycle, "dispatch_next_driver", dispatch)

    response = client.post(
        f"/api/v1/driver/rides/{RIDE_ID}/cancel",
        json={"reason": "Vehicle problem"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "requested"
    assert response.json()["dispatch_status"] == "offered"
    assert dispatch_calls == [(db, RIDE_ID, 1)]
    assert db.ride["assigned_driver_id"] is None
    assert db.ride["assigned_vehicle_id"] is None
    assert db.ride["status"] == "requested"
    assert db.recorded_events[0]["event_type"] == "ride_cancelled_by_driver"
    assert any(
        "driver_user_id = :driver_id" in sql
        and "status = 'accepted'" in sql
        for sql, _ in db.execute_calls
    )
    assert db.commit_count == 2


def test_driver_cannot_cancel_ongoing_ride(monkeypatch):
    db = LifecycleSession(ride=ride_state(status="ongoing"))
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/cancel")

    assert response.status_code == 409
    assert db.ride["status"] == "ongoing"
    assert db.commit_count == 0
    assert not db.recorded_events


def test_passenger_history_filters_by_authenticated_user_and_role(monkeypatch):
    rows = [
        history_row(UUID(int=index), NOW - timedelta(days=index))
        for index in range(1, 4)
    ]
    db = LifecycleSession(roles=("passenger",), history=rows)
    client = build_client(monkeypatch, db, user_id=PASSENGER_ID, roles=("passenger",))

    response = client.get("/api/v1/me/rides?role=passenger&limit=2")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    history_query, params = next(
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("SELECT id AS ride_id")
    )
    assert "WHERE passenger_id = :user_id" in history_query
    assert params["user_id"] == PASSENGER_ID
    assert response.json()["next_cursor"]


def test_driver_history_filters_assigned_driver_and_cursor_paginates(monkeypatch):
    rows = [
        history_row(UUID(int=index), NOW - timedelta(days=index))
        for index in range(1, 4)
    ]
    db = LifecycleSession(roles=("driver",), history=rows)
    client = build_client(monkeypatch, db, user_id=DRIVER_ID, roles=("driver",))

    first_page = client.get("/api/v1/me/rides?role=driver&limit=2")

    assert first_page.status_code == 200
    cursor = first_page.json()["next_cursor"]
    second_page = client.get(
        "/api/v1/me/rides",
        params={"role": "driver", "limit": 2, "cursor": cursor},
    )

    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1
    history_queries = [
        (sql, params)
        for sql, params in db.execute_calls
        if sql.startswith("SELECT id AS ride_id")
    ]
    assert all("WHERE assigned_driver_id = :user_id" in sql for sql, _ in history_queries)
    assert history_queries[1][1]["cursor_ride_id"] == UUID(
        first_page.json()["items"][-1]["ride_id"]
    )
    assert "(created_at, id) < (:cursor_created_at, :cursor_ride_id)" in history_queries[1][0]


def test_history_rejects_role_not_owned_by_authenticated_user(monkeypatch):
    db = LifecycleSession(roles=("driver",))
    client = build_client(monkeypatch, db, user_id=DRIVER_ID, roles=("driver",))

    response = client.get("/api/v1/me/rides?role=passenger")

    assert response.status_code == 403
    assert not any(
        sql.startswith("SELECT id AS ride_id")
        for sql, _ in db.execute_calls
    )


def test_unauthorized_user_cannot_read_ride_events(monkeypatch):
    db = LifecycleSession(roles=("passenger",), event_access=False)
    client = build_client(monkeypatch, db, user_id=OTHER_PASSENGER_ID, roles=("passenger",))

    response = client.get(f"/api/v1/rides/{RIDE_ID}/events")

    assert response.status_code == 404
    assert not any(
        sql.startswith("SELECT id AS event_id")
        for sql, _ in db.execute_calls
    )


def test_ride_events_are_ordered_and_metadata_is_filtered(monkeypatch):
    db = LifecycleSession(
        roles=("passenger",),
        event_access=True,
        events=[
            {
                "event_id": UUID(int=2),
                "event_type": "ride_started",
                "actor_type": "driver",
                "created_at": NOW,
                "from_status": "arriving",
                "to_status": "ongoing",
                "metadata": {"access_token": "do-not-return"},
            },
            {
                "event_id": UUID(int=1),
                "event_type": "ride_requested",
                "actor_type": "passenger",
                "created_at": NOW - timedelta(minutes=1),
                "from_status": None,
                "to_status": "requested",
                "metadata": {
                    "client_request_id": "safe-id",
                    "access_token": "do-not-return",
                    "reason": "private text",
                },
            }
        ],
    )
    client = build_client(monkeypatch, db, user_id=PASSENGER_ID, roles=("passenger",))

    response = client.get(f"/api/v1/rides/{RIDE_ID}/events")

    assert response.status_code == 200
    assert [event["event_type"] for event in response.json()] == [
        "ride_requested",
        "ride_started",
    ]
    event = response.json()[0]
    assert event["metadata"] == {"client_request_id": "safe-id"}
    assert "access_token" not in event["metadata"]
    assert "actor_user_id" not in event


def test_stale_transition_is_rejected_without_overwriting_ride(monkeypatch):
    db = LifecycleSession(ride=ride_state(status="ongoing"))
    client = build_client(monkeypatch, db)

    response = client.post(f"/api/v1/driver/rides/{RIDE_ID}/arrived")

    assert response.status_code == 409
    assert db.ride["status"] == "ongoing"
    assert not db.recorded_events
    assert not any(
        "set status = 'arriving'" in sql.lower()
        for sql, _ in db.execute_calls
    )
    lock_sql = next(sql for sql, _ in db.execute_calls if "FOR UPDATE" in sql)
    assert "FOR UPDATE" in lock_sql
