import asyncio
from uuid import UUID

from fastapi import HTTPException

from app.schemas.ride import GeoJSONLineString, RoutingResult
from app.services import dispatch_service
from app.services.dispatch_service import dispatch_next_driver


RIDE_ID = UUID("a8493516-3fd4-4a14-9b0a-a7e5dfac01ab")
DRIVER_ONE = UUID("6f8e9d26-eef8-4ef9-b567-cbc5ab05d9e3")
DRIVER_TWO = UUID("7f8e9d26-eef8-4ef9-b567-cbc5ab05d9e4")
NOW_OFFER_ID = UUID("8f8e9d26-eef8-4ef9-b567-cbc5ab05d9e5")


class MockResult:
    def __init__(self, *, row=None, rows=(), scalar=None):
        self.row = row
        self.rows = list(rows)
        self.scalar = scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar


def candidate(driver_id, lng, eta_idle=10, pickup_distance=300):
    return {
        "driver_user_id": driver_id,
        "vehicle_id": UUID(int=driver_id.int % 100000 + 1),
        "driver_lng": lng,
        "driver_lat": 12.30,
        "pickup_distance_m": pickup_distance,
        "idle_seconds": eta_idle,
    }


class MockDispatchSession:
    def __init__(
        self,
        *,
        candidates=(),
        existing_offer=None,
        insert_conflicts=(),
        ride_status="requested",
    ):
        self.candidates = list(candidates)
        self.existing_offer = existing_offer
        self.insert_conflicts = set(insert_conflicts)
        self.ride_status = ride_status
        self.execute_calls = []
        self.offer_inserts = []
        self.events = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        normalized_sql = sql.lower()
        params = params or {}
        self.execute_calls.append((sql, params))

        if (
            normalized_sql.startswith("select status, assigned_driver_id")
            and "from public.rides" in normalized_sql
        ):
            return MockResult(
                row={
                    "status": self.ride_status,
                    "assigned_driver_id": None,
                    "pickup_lng": 76.6394,
                    "pickup_lat": 12.2958,
                }
            )
        if "from public.ride_offers" in normalized_sql and "expires_at > now()" in normalized_sql:
            return MockResult(row=self.existing_offer)
        if "st_dwithin" in normalized_sql:
            return MockResult(rows=self.candidates[:10])
        if "coalesce(max(offer_number)" in normalized_sql:
            return MockResult(scalar=1)
        if normalized_sql.startswith("insert into public.ride_offers"):
            self.offer_inserts.append(params)
            if params["driver_user_id"] in self.insert_conflicts:
                return MockResult()
            return MockResult(row={"id": NOW_OFFER_ID})
        if normalized_sql.startswith("update public.rides"):
            return MockResult(row={"id": RIDE_ID})
        if "insert into public.ride_events" in normalized_sql:
            self.events.append(params)
            return MockResult()
        if "from public.ride_offers" in normalized_sql:
            return MockResult()
        raise AssertionError(f"Unexpected SQL statement: {sql}")


def mock_route_times(monkeypatch, etas):
    calls = []

    async def get_route(pickup, destination):
        calls.append((pickup, destination))
        return RoutingResult(
            distance_m=1000,
            duration_s=etas[destination.lng],
            geometry=GeoJSONLineString(
                type="LineString",
                coordinates=[[pickup.lng, pickup.lat], [destination.lng, destination.lat]],
            ),
        )

    monkeypatch.setattr(dispatch_service, "get_driving_route", get_route)
    return calls


def test_dispatch_filters_candidates_ranks_by_road_eta_and_offers_one(monkeypatch):
    first = candidate(DRIVER_ONE, 76.64, eta_idle=600)
    second = candidate(DRIVER_TWO, 76.65, eta_idle=30)
    db = MockDispatchSession(candidates=[first, second])
    calls = mock_route_times(monkeypatch, {76.64: 120, 76.65: 45})

    result = asyncio.run(dispatch_next_driver(db, RIDE_ID))

    assert result.status == "offered"
    assert result.driver_user_id == DRIVER_TWO
    assert result.pickup_eta_s == 45
    assert len(calls) == 2
    assert len(db.offer_inserts) == 1
    assert db.offer_inserts[0]["driver_user_id"] == DRIVER_TWO
    assert db.offer_inserts[0]["offer_number"] == 1
    assert db.offer_inserts[0]["timeout_seconds"] > 0

    candidate_sql, params = next(
        (sql, params) for sql, params in db.execute_calls if "ST_DWithin" in sql
    )
    assert "verification_status = 'verified'" in candidate_sql
    assert "availability_status = 'available'" in candidate_sql
    assert "received_at >= now()" in candidate_sql
    assert "is_active = TRUE" in candidate_sql
    assert "NOT EXISTS" in candidate_sql
    assert "LIMIT 10" in candidate_sql
    assert "FOR UPDATE OF dp SKIP LOCKED" in candidate_sql
    assert params["search_radius_m"] > 0
    assert len(db.events) == 1


def test_dispatch_skips_driver_with_conflicting_active_offer(monkeypatch):
    first = candidate(DRIVER_ONE, 76.64)
    second = candidate(DRIVER_TWO, 76.65)
    db = MockDispatchSession(
        candidates=[first, second],
        insert_conflicts=(DRIVER_ONE,),
    )
    mock_route_times(monkeypatch, {76.64: 30, 76.65: 40})

    result = asyncio.run(dispatch_next_driver(db, RIDE_ID))

    assert result.driver_user_id == DRIVER_TWO
    assert [row["driver_user_id"] for row in db.offer_inserts] == [
        DRIVER_ONE,
        DRIVER_TWO,
    ]
    assert len(db.events) == 1


def test_dispatch_query_excludes_drivers_previously_offered(monkeypatch):
    second = candidate(DRIVER_TWO, 76.65)
    db = MockDispatchSession(candidates=[second])
    mock_route_times(monkeypatch, {76.65: 40})

    result = asyncio.run(dispatch_next_driver(db, RIDE_ID))

    sql = next(sql for sql, _ in db.execute_calls if "ST_DWithin" in sql)
    assert "previous_offer.driver_user_id = dp.user_id" in sql
    assert result.driver_user_id == DRIVER_TWO


def test_dispatch_marks_ride_no_driver_found_when_candidates_are_empty():
    db = MockDispatchSession(candidates=[])

    result = asyncio.run(dispatch_next_driver(db, RIDE_ID))

    assert result.status == "no_driver_found"
    assert any(
        "SET status = 'no_driver_found'" in sql for sql, _ in db.execute_calls
    )
    assert db.events[0]["event_type"] == "no_driver_found"


def test_dispatch_does_not_duplicate_an_existing_offer():
    db = MockDispatchSession(
        existing_offer={
            "id": NOW_OFFER_ID,
            "driver_user_id": DRIVER_ONE,
            "is_active": True,
        }
    )

    result = asyncio.run(dispatch_next_driver(db, RIDE_ID))

    assert result.status == "offered"
    assert result.offer_id == NOW_OFFER_ID
    assert not db.offer_inserts


def test_routing_outage_leaves_dispatch_pending(monkeypatch):
    db = MockDispatchSession(candidates=[candidate(DRIVER_ONE, 76.64)])

    async def unavailable(*args):
        raise HTTPException(status_code=503, detail="unavailable")

    monkeypatch.setattr(dispatch_service, "get_driving_route", unavailable)

    result = asyncio.run(dispatch_next_driver(db, RIDE_ID))

    assert result.status == "pending"
    assert not db.offer_inserts
    assert not db.events
