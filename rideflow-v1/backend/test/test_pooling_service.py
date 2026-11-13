import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException, status

from app.schemas.location import Coordinate
from app.schemas.ride import GeoJSONLineString
from app.services import pooling_service
from app.services.pooling_service import (
    _RideRoute,
    _feasible_routes,
    _stop_sequences,
    preview_pool,
)
from app.services.routing_service import (
    MultiStopRoutingResult,
    RoutingLeg,
    RoutingResult,
)


RIDE_ID = UUID("a8493516-3fd4-4a14-9b0a-a7e5dfac01ab")
PASSENGER_ID = UUID("5b2b874a-9b9b-4dd1-8c76-83f173ea0843")
PARTNER_RIDE_ID = UUID("c8493516-3fd4-4a14-9b0a-a7e5dfac01ab")
OTHER_PASSENGER_ID = UUID("6b2b874a-9b9b-4dd1-8c76-83f173ea0843")


class MockResult:
    def __init__(self, *, row=None, rows=()):
        self.row = row
        self.rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return self.rows


def primary_row(**overrides):
    row = {
        "ride_id": RIDE_ID,
        "status": "requested",
        "pooling_opt_in": True,
        "assigned_driver_id": None,
        "assigned_vehicle_id": None,
        "pickup_lat": 12.0,
        "pickup_lng": 76.0,
        "destination_lat": 12.1,
        "destination_lng": 76.1,
        "pickup_is_point": True,
        "destination_is_point": True,
        "pickup_not_empty": True,
        "destination_not_empty": True,
    }
    row.update(overrides)
    return row


def partner_row(**overrides):
    row = {
        "ride_id": PARTNER_RIDE_ID,
        "pickup_lat": 12.005,
        "pickup_lng": 76.005,
        "destination_lat": 12.105,
        "destination_lng": 76.105,
    }
    row.update(overrides)
    return row


class MockSession:
    def __init__(self, *, primary=None, candidates=()):
        self.primary = primary
        self.candidates = list(candidates)
        self.statements = []
        self.parameters = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        self.parameters.append(params or {})
        if "FROM public.rides AS primary_ride" in sql:
            return MockResult(rows=self.candidates)
        if "FROM public.rides AS ride" in sql:
            return MockResult(row=self.primary)
        raise AssertionError(f"Unexpected SQL statement: {sql}")


def multi_route(
    distances=(2000, 9000, 2000),
    durations=(60, 540, 60),
    *,
    total_distance=None,
    total_duration=None,
):
    return MultiStopRoutingResult(
        distance_m=sum(distances) if total_distance is None else total_distance,
        duration_s=sum(durations) if total_duration is None else total_duration,
        geometry=GeoJSONLineString(
            type="LineString",
            coordinates=[[76.0, 12.0], [76.1, 12.1]],
        ),
        legs=tuple(
            RoutingLeg(distance_m=distance, duration_s=duration)
            for distance, duration in zip(distances, durations)
        ),
    )


def install_route_mocks(monkeypatch, *, pooled_route=None):
    individual_calls = []
    pooled_calls = []

    async def individual_route(pickup, destination):
        individual_calls.append((pickup, destination))
        return RoutingResult(
            distance_m=10_000,
            duration_s=600,
            geometry=GeoJSONLineString(
                type="LineString",
                coordinates=[
                    [pickup.lng, pickup.lat],
                    [destination.lng, destination.lat],
                ],
            ),
        )

    async def multi_stop_routes(stops):
        pooled_calls.append(stops)
        return [pooled_route or multi_route()]

    monkeypatch.setattr(pooling_service, "get_driving_route", individual_route)
    monkeypatch.setattr(
        pooling_service,
        "get_multi_stop_driving_routes",
        multi_stop_routes,
    )
    return individual_calls, pooled_calls


def test_pooling_stop_sequences_enforce_pickup_before_each_dropoff():
    sequences = _stop_sequences()

    assert len(sequences) == 3
    assert all(sequence[0] == "pickup_a" for sequence in sequences)
    assert all(
        sequence.index("pickup_a") < sequence.index("dropoff_a")
        and sequence.index("pickup_b") < sequence.index("dropoff_b")
        for sequence in sequences
    )


def test_pool_preview_selects_minimum_duration_feasible_route_and_saves_fares(
    monkeypatch,
):
    install_route_mocks(monkeypatch)
    db = MockSession(primary=primary_row(), candidates=[partner_row()])

    preview = asyncio.run(
        preview_pool(db, ride_id=RIDE_ID, passenger_id=PASSENGER_ID)
    )

    assert preview.pool_available is True
    assert preview.pooled_duration_s == 660
    assert preview.pooled_distance_m == 13_000
    assert preview.route_geometry.type == "LineString"
    assert preview.optimized_stops[0].passenger_label == "A"
    assert preview.optimized_stops[0].stop_type == "pickup"
    for passenger in ("A", "B"):
        pickup_index = next(
            index
            for index, stop in enumerate(preview.optimized_stops)
            if stop.passenger_label == passenger and stop.stop_type == "pickup"
        )
        dropoff_index = next(
            index
            for index, stop in enumerate(preview.optimized_stops)
            if stop.passenger_label == passenger and stop.stop_type == "dropoff"
        )
        assert pickup_index < dropoff_index

    comparison = preview.fare_comparison
    assert comparison is not None
    assert comparison.estimates_only is True
    assert comparison.discount_percent == 10
    assert comparison.total_separate_fare_estimate == 320
    assert comparison.pooled_total_fare_estimate == 177.3
    assert comparison.estimated_total_passenger_savings > 0
    assert sum(
        fare.pooled_fare_estimate for fare in comparison.passenger_fares
    ) == comparison.pooled_total_fare_estimate
    assert len(db.statements) == 2
    assert all(sql.startswith("SELECT") for sql in db.statements)


@pytest.mark.parametrize(
    "route",
    [
        multi_route(distances=(20_000, 20_000, 20_000)),
        multi_route(durations=(2000, 2000, 2000)),
    ],
)
def test_pool_preview_rejects_routes_over_detour_or_duration_limits(
    monkeypatch,
    route,
):
    install_route_mocks(monkeypatch, pooled_route=route)
    db = MockSession(primary=primary_row(), candidates=[partner_row()])

    preview = asyncio.run(
        preview_pool(db, ride_id=RIDE_ID, passenger_id=PASSENGER_ID)
    )

    assert preview.pool_available is False
    assert preview.fare_comparison is None


def test_pool_preview_rejects_route_without_estimated_fare_savings(monkeypatch):
    no_savings_route = multi_route(
        distances=(26_000 / 3, 26_000 / 3, 26_000 / 3),
        durations=(600, 600, 600),
        total_distance=26_000,
        total_duration=1800,
    )
    install_route_mocks(monkeypatch, pooled_route=no_savings_route)
    db = MockSession(primary=primary_row(), candidates=[partner_row()])

    preview = asyncio.run(
        preview_pool(db, ride_id=RIDE_ID, passenger_id=PASSENGER_ID)
    )

    assert preview.pool_available is False
    assert preview.fare_comparison is None


def test_primary_ride_owner_is_enforced_and_opt_in_is_required():
    unauthorized_db = MockSession(primary=None)
    with pytest.raises(HTTPException) as unauthorized:
        asyncio.run(
            preview_pool(
                unauthorized_db,
                ride_id=RIDE_ID,
                passenger_id=OTHER_PASSENGER_ID,
            )
        )
    assert unauthorized.value.status_code == status.HTTP_404_NOT_FOUND

    opt_out_db = MockSession(primary=primary_row(pooling_opt_in=False))
    with pytest.raises(HTTPException) as opted_out:
        asyncio.run(
            preview_pool(
                opt_out_db,
                ride_id=RIDE_ID,
                passenger_id=PASSENGER_ID,
            )
        )
    assert opted_out.value.status_code == status.HTTP_409_CONFLICT
    assert len(opt_out_db.statements) == 1


def test_no_candidate_returns_explanation_without_routing():
    db = MockSession(primary=primary_row())

    preview = asyncio.run(
        preview_pool(db, ride_id=RIDE_ID, passenger_id=PASSENGER_ID)
    )

    assert preview.pool_available is False
    assert "No other pending" in preview.explanation
    assert len(db.statements) == 2
    assert all(sql.startswith("SELECT") for sql in db.statements)


def test_candidate_query_uses_postgis_eligibility_and_vehicle_capacity():
    db = MockSession(primary=primary_row(), candidates=())

    asyncio.run(preview_pool(db, ride_id=RIDE_ID, passenger_id=PASSENGER_ID))

    sql = db.statements[1]
    params = db.parameters[1]
    assert "partner.status = 'requested'" in sql
    assert "partner.pooling_opt_in = TRUE" in sql
    assert "partner.assigned_driver_id IS NULL" in sql
    assert "partner.assigned_vehicle_id IS NULL" in sql
    assert "ST_DWithin(" in sql
    assert "ST_Distance(" in sql
    assert "vehicle.seat_capacity >= :required_seats" in sql
    assert "partner.passenger_id" not in sql.split("SELECT", 1)[1].split("FROM", 1)[0]
    assert params["pickup_proximity_meters"] == 2000
    assert params["destination_proximity_meters"] == 3000
    assert params["required_seats"] == 2
    assert params["candidate_limit"] <= 25


def test_osrm_503_is_propagated(monkeypatch):
    async def individual_route(*args):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Routing service is unavailable.",
        )

    monkeypatch.setattr(pooling_service, "get_driving_route", individual_route)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            pooling_service._preview_candidate(
                _RideRoute(
                    pickup=Coordinate(lat=12, lng=76),
                    destination=Coordinate(lat=12.1, lng=76.1),
                ),
                _RideRoute(
                    pickup=Coordinate(lat=12.01, lng=76.01),
                    destination=Coordinate(lat=12.11, lng=76.11),
                ),
            )
        )
    assert error.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
