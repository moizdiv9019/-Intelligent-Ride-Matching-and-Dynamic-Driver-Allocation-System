"""Read-only Stage A pooling compatibility and route/fare preview."""

import itertools
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.location import Coordinate
from app.schemas.ride import (
    PassengerPoolEstimate,
    PoolingFareComparison,
    PoolingPreviewResponse,
    PoolingStop,
)
from app.services.fare_service import calculate_fare
from app.services.routing_service import (
    MultiStopRoutingResult,
    get_driving_route,
    get_multi_stop_driving_routes,
)


_CANDIDATE_LIMIT = 25
_CENT = Decimal("0.01")
_STOP_NAMES = ("pickup_a", "dropoff_a", "pickup_b", "dropoff_b")


@dataclass(frozen=True)
class _RideRoute:
    pickup: Coordinate
    destination: Coordinate


@dataclass(frozen=True)
class _RouteChoice:
    route: MultiStopRoutingResult
    stop_order: tuple[str, ...]
    passenger_a_distance_m: float
    passenger_a_duration_s: float
    passenger_b_distance_m: float
    passenger_b_duration_s: float


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _coordinates_from_row(row, prefix: str) -> _RideRoute:
    return _RideRoute(
        pickup=Coordinate(
            lat=row[f"{prefix}pickup_lat"],
            lng=row[f"{prefix}pickup_lng"],
        ),
        destination=Coordinate(
            lat=row[f"{prefix}destination_lat"],
            lng=row[f"{prefix}destination_lng"],
        ),
    )


def _stop_sequences() -> list[tuple[str, ...]]:
    sequences = []
    for sequence in itertools.permutations(_STOP_NAMES):
        indexes = {stop: index for index, stop in enumerate(sequence)}
        if (
            sequence[0] == "pickup_a"
            and indexes["pickup_a"] < indexes["dropoff_a"]
            and indexes["pickup_b"] < indexes["dropoff_b"]
        ):
            sequences.append(sequence)
    return sequences


def _sum_passenger_legs(
    route: MultiStopRoutingResult,
    sequence: tuple[str, ...],
    passenger_label: str,
) -> tuple[float, float]:
    pickup_index = sequence.index(f"pickup_{passenger_label.lower()}")
    dropoff_index = sequence.index(f"dropoff_{passenger_label.lower()}")
    legs = route.legs[pickup_index:dropoff_index]
    return (
        sum(leg.distance_m for leg in legs),
        sum(leg.duration_s for leg in legs),
    )


def _ordered_coordinates(
    sequence: tuple[str, ...],
    primary: _RideRoute,
    partner: _RideRoute,
) -> list[Coordinate]:
    coordinates = {
        "pickup_a": primary.pickup,
        "dropoff_a": primary.destination,
        "pickup_b": partner.pickup,
        "dropoff_b": partner.destination,
    }
    return [coordinates[stop] for stop in sequence]


async def _feasible_routes(
    primary: _RideRoute,
    partner: _RideRoute,
    *,
    primary_individual_distance_m: float,
    primary_individual_duration_s: float,
    partner_individual_distance_m: float,
    partner_individual_duration_s: float,
) -> list[_RouteChoice]:
    choices: list[_RouteChoice] = []
    for sequence in _stop_sequences():
        try:
            alternatives = await get_multi_stop_driving_routes(
                _ordered_coordinates(sequence, primary, partner)
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
                continue
            raise

        for route in alternatives:
            a_distance, a_duration = _sum_passenger_legs(route, sequence, "A")
            b_distance, b_duration = _sum_passenger_legs(route, sequence, "B")
            a_detour = max(0.0, a_distance - primary_individual_distance_m)
            b_detour = max(0.0, b_distance - partner_individual_distance_m)
            a_detour_percent = (
                a_detour / primary_individual_distance_m * 100
                if primary_individual_distance_m > 0
                else (0.0 if a_detour == 0 else float("inf"))
            )
            b_detour_percent = (
                b_detour / partner_individual_distance_m * 100
                if partner_individual_distance_m > 0
                else (0.0 if b_detour == 0 else float("inf"))
            )
            a_additional_duration = max(
                0.0,
                a_duration - primary_individual_duration_s,
            )
            b_additional_duration = max(
                0.0,
                b_duration - partner_individual_duration_s,
            )
            if (
                a_detour_percent > settings.pooling_max_detour_percent
                or b_detour_percent > settings.pooling_max_detour_percent
                or a_additional_duration
                > settings.pooling_max_additional_duration_seconds
                or b_additional_duration
                > settings.pooling_max_additional_duration_seconds
            ):
                continue
            choices.append(
                _RouteChoice(
                    route=route,
                    stop_order=sequence,
                    passenger_a_distance_m=a_distance,
                    passenger_a_duration_s=a_duration,
                    passenger_b_distance_m=b_distance,
                    passenger_b_duration_s=b_duration,
                )
            )
    if not choices:
        return []
    return sorted(
        choices,
        key=lambda choice: (
            choice.route.duration_s,
            choice.route.distance_m,
            choice.stop_order,
        ),
    )


def _fare_comparison(
    choice: _RouteChoice,
    *,
    primary_individual_distance_m: float,
    primary_individual_duration_s: float,
    partner_individual_distance_m: float,
    partner_individual_duration_s: float,
) -> PoolingFareComparison | None:
    primary_fare = calculate_fare(
        primary_individual_distance_m,
        primary_individual_duration_s,
    ).estimated_total
    partner_fare = calculate_fare(
        partner_individual_distance_m,
        partner_individual_duration_s,
    ).estimated_total
    total_separate = _round_money(
        Decimal(str(primary_fare)) + Decimal(str(partner_fare))
    )
    pooled_base = Decimal(
        str(
            calculate_fare(
                choice.route.distance_m,
                choice.route.duration_s,
            ).estimated_total
        )
    )
    discount = Decimal(str(settings.pooling_demo_discount_percent)) / Decimal("100")
    pooled_total = _round_money(pooled_base * (Decimal("1") - discount))
    savings = _round_money(total_separate - pooled_total)
    if savings <= 0 or total_separate <= 0:
        return None

    passenger_a_pooled_fare = _round_money(
        pooled_total * Decimal(str(primary_fare)) / total_separate
    )
    passenger_b_pooled_fare = pooled_total - passenger_a_pooled_fare
    a_detour = max(
        0.0,
        choice.passenger_a_distance_m - primary_individual_distance_m,
    )
    b_detour = max(
        0.0,
        choice.passenger_b_distance_m - partner_individual_distance_m,
    )
    a_detour_percent = (
        a_detour / primary_individual_distance_m * 100
        if primary_individual_distance_m > 0
        else 0.0
    )
    b_detour_percent = (
        b_detour / partner_individual_distance_m * 100
        if partner_individual_distance_m > 0
        else 0.0
    )
    return PoolingFareComparison(
        discount_percent=settings.pooling_demo_discount_percent,
        total_separate_fare_estimate=float(total_separate),
        pooled_base_fare_estimate=float(pooled_base),
        pooled_total_fare_estimate=float(pooled_total),
        estimated_total_passenger_savings=float(savings),
        passenger_fares=[
            PassengerPoolEstimate(
                passenger_label="A",
                individual_distance_m=primary_individual_distance_m,
                individual_duration_s=primary_individual_duration_s,
                individual_fare_estimate=primary_fare,
                pooled_distance_m=choice.passenger_a_distance_m,
                pooled_duration_s=choice.passenger_a_duration_s,
                detour_distance_m=a_detour,
                detour_percent=a_detour_percent,
                additional_duration_s=max(
                    0.0,
                    choice.passenger_a_duration_s
                    - primary_individual_duration_s,
                ),
                pooled_fare_estimate=float(passenger_a_pooled_fare),
            ),
            PassengerPoolEstimate(
                passenger_label="B",
                individual_distance_m=partner_individual_distance_m,
                individual_duration_s=partner_individual_duration_s,
                individual_fare_estimate=partner_fare,
                pooled_distance_m=choice.passenger_b_distance_m,
                pooled_duration_s=choice.passenger_b_duration_s,
                detour_distance_m=b_detour,
                detour_percent=b_detour_percent,
                additional_duration_s=max(
                    0.0,
                    choice.passenger_b_duration_s
                    - partner_individual_duration_s,
                ),
                pooled_fare_estimate=float(passenger_b_pooled_fare),
            ),
        ],
    )


async def _preview_candidate(
    primary: _RideRoute,
    partner: _RideRoute,
) -> PoolingPreviewResponse | None:
    try:
        individual_a = await get_driving_route(primary.pickup, primary.destination)
        individual_b = await get_driving_route(partner.pickup, partner.destination)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
            return None
        raise

    choices = await _feasible_routes(
        primary,
        partner,
        primary_individual_distance_m=individual_a.distance_m,
        primary_individual_duration_s=individual_a.duration_s,
        partner_individual_distance_m=individual_b.distance_m,
        partner_individual_duration_s=individual_b.duration_s,
    )
    for choice in choices:
        fare_comparison = _fare_comparison(
            choice,
            primary_individual_distance_m=individual_a.distance_m,
            primary_individual_duration_s=individual_a.duration_s,
            partner_individual_distance_m=individual_b.distance_m,
            partner_individual_duration_s=individual_b.duration_s,
        )
        if fare_comparison is None:
            continue

        stops = []
        coordinates = {
            "pickup_a": primary.pickup,
            "dropoff_a": primary.destination,
            "pickup_b": partner.pickup,
            "dropoff_b": partner.destination,
        }
        for stop_name in choice.stop_order:
            stops.append(
                PoolingStop(
                    passenger_label="A" if stop_name.endswith("_a") else "B",
                    stop_type=(
                        "pickup" if stop_name.startswith("pickup") else "dropoff"
                    ),
                    coordinate=coordinates[stop_name],
                )
            )
        return PoolingPreviewResponse(
            pool_available=True,
            explanation=(
                "A compatible pending request was found. Route and fare values are "
                "estimates; pooled fare is apportioned in proportion to individual "
                "fare estimates. No rides were changed."
            ),
            optimized_stops=stops,
            route_geometry=choice.route.geometry,
            pooled_distance_m=choice.route.distance_m,
            pooled_duration_s=choice.route.duration_s,
            fare_comparison=fare_comparison,
        )
    return None


async def preview_pool(
    db: Session,
    *,
    ride_id: UUID,
    passenger_id: UUID,
) -> PoolingPreviewResponse:
    """Return one ephemeral preview; Stage A has no persisted pool membership."""
    primary_row = db.execute(
        text(
            """
            SELECT
                ride.id AS ride_id,
                ride.status,
                ride.pooling_opt_in,
                ride.assigned_driver_id,
                ride.assigned_vehicle_id,
                ST_Y(ride.pickup_location::geometry) AS pickup_lat,
                ST_X(ride.pickup_location::geometry) AS pickup_lng,
                ST_Y(ride.destination_location::geometry) AS destination_lat,
                ST_X(ride.destination_location::geometry) AS destination_lng,
                ST_GeometryType(ride.pickup_location::geometry) = 'ST_Point'
                    AS pickup_is_point,
                ST_GeometryType(ride.destination_location::geometry) = 'ST_Point'
                    AS destination_is_point,
                NOT ST_IsEmpty(ride.pickup_location::geometry) AS pickup_not_empty,
                NOT ST_IsEmpty(ride.destination_location::geometry)
                    AS destination_not_empty
            FROM public.rides AS ride
            WHERE ride.id = :ride_id
              AND ride.passenger_id = :passenger_id
            """
        ),
        {"ride_id": ride_id, "passenger_id": passenger_id},
    ).mappings().first()
    if primary_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found.",
        )
    if (
        primary_row["status"] != "requested"
        or primary_row["assigned_driver_id"] is not None
        or primary_row["assigned_vehicle_id"] is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only unassigned requested rides can be previewed for pooling.",
        )
    if not primary_row["pooling_opt_in"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Enable pooling opt-in when requesting the ride to preview pooling.",
        )
    if (
        not primary_row["pickup_is_point"]
        or not primary_row["destination_is_point"]
        or not primary_row["pickup_not_empty"]
        or not primary_row["destination_not_empty"]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ride pickup and destination must be valid points.",
        )
    if settings.pooling_max_passengers < 2:
        return PoolingPreviewResponse(
            pool_available=False,
            explanation="Pooling requires capacity for both passengers.",
        )
    primary = _coordinates_from_row(primary_row, "")

    candidates = db.execute(
        text(
            """
            SELECT
                partner.id AS ride_id,
                ST_Y(partner.pickup_location::geometry) AS pickup_lat,
                ST_X(partner.pickup_location::geometry) AS pickup_lng,
                ST_Y(partner.destination_location::geometry) AS destination_lat,
                ST_X(partner.destination_location::geometry) AS destination_lng
            FROM public.rides AS primary_ride
            JOIN public.rides AS partner
              ON partner.id <> primary_ride.id
            WHERE primary_ride.id = :ride_id
              AND partner.passenger_id <> :passenger_id
              AND partner.status = 'requested'
              AND partner.pooling_opt_in = TRUE
              AND partner.assigned_driver_id IS NULL
              AND partner.assigned_vehicle_id IS NULL
              AND ST_GeometryType(partner.pickup_location::geometry) = 'ST_Point'
              AND ST_GeometryType(partner.destination_location::geometry) = 'ST_Point'
              AND NOT ST_IsEmpty(partner.pickup_location::geometry)
              AND NOT ST_IsEmpty(partner.destination_location::geometry)
              AND ST_DWithin(
                    primary_ride.pickup_location,
                    partner.pickup_location,
                    :pickup_proximity_meters
              )
              AND ST_DWithin(
                    primary_ride.destination_location,
                    partner.destination_location,
                    :destination_proximity_meters
              )
              AND EXISTS (
                    SELECT 1
                    FROM public.vehicles AS vehicle
                    WHERE vehicle.is_active = TRUE
                      AND vehicle.seat_capacity >= :required_seats
              )
            ORDER BY
                ST_Distance(
                    primary_ride.pickup_location,
                    partner.pickup_location
                ),
                partner.id
            LIMIT :candidate_limit
            """
        ),
        {
            "ride_id": ride_id,
            "passenger_id": passenger_id,
            "pickup_proximity_meters": settings.pooling_pickup_proximity_meters,
            "destination_proximity_meters": (
                settings.pooling_destination_proximity_meters
            ),
            "required_seats": 2,
            "candidate_limit": _CANDIDATE_LIMIT,
        },
    ).mappings().all()
    if not candidates:
        return PoolingPreviewResponse(
            pool_available=False,
            explanation=(
                "No other pending, opted-in request meets the pickup, destination, "
                "and active-vehicle capacity requirements."
            ),
        )

    for candidate_row in candidates:
        partner = _coordinates_from_row(candidate_row, "")
        preview = await _preview_candidate(primary, partner)
        if preview is not None:
            return preview
    return PoolingPreviewResponse(
        pool_available=False,
        explanation=(
            "Nearby opted-in requests were found, but none met route detour, "
            "additional-duration, and fare-savings limits."
        ),
    )
