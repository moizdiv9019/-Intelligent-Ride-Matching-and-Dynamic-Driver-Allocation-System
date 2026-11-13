import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser
from app.db.session import get_db
from app.schemas.ride import (
    GeoJSONLineString,
    RideCreateRequest,
    RideDetailsResponse,
    RideEstimateRequest,
    RideEstimateResponse,
    RideVehicleSummary,
)
from app.services.fare_service import calculate_fare
from app.services.dispatch_service import dispatch_next_driver
from app.services.ride_event_service import record_ride_event
from app.services.routing_service import get_driving_route


router = APIRouter(prefix="/rides", tags=["rides"])


def _read_ride(
    db: Session,
    ride_id: UUID,
    *,
    passenger_id: UUID | None = None,
    requester_id: UUID | None = None,
) -> RideDetailsResponse | None:
    access_filter = ""
    params: dict[str, UUID] = {"ride_id": ride_id}
    if passenger_id is not None:
        access_filter = "AND ride.passenger_id = :passenger_id"
        params["passenger_id"] = passenger_id
    elif requester_id is not None:
        access_filter = """
            AND (
                ride.passenger_id = :requester_id
                OR ride.assigned_driver_id = :requester_id
            )
        """
        params["requester_id"] = requester_id

    row = db.execute(
        text(
            f"""
            SELECT
                ride.id AS ride_id,
                ride.status,
                ST_Y(ride.pickup_location::geometry) AS pickup_lat,
                ST_X(ride.pickup_location::geometry) AS pickup_lng,
                ST_Y(ride.destination_location::geometry) AS destination_lat,
                ST_X(ride.destination_location::geometry) AS destination_lng,
                ride.pickup_address,
                ride.destination_address,
                ride.estimated_distance_m,
                ride.estimated_duration_s,
                ride.estimated_fare,
                ride.final_distance_m,
                ride.final_fare,
                ride.currency,
                ride.fare_breakdown,
                ST_AsGeoJSON(ride.route_geom)::jsonb AS route_geometry,
                ride.assigned_driver_id,
                vehicle.id AS vehicle_id,
                vehicle.vehicle_type,
                ride.requested_at,
                ride.accepted_at,
                ride.arrived_at,
                ride.started_at,
                ride.completed_at,
                ride.cancelled_at,
                CASE
                    WHEN ride.status = 'no_driver_found' THEN 'no_driver_found'
                    WHEN EXISTS (
                        SELECT 1
                        FROM public.ride_offers AS active_offer
                        WHERE active_offer.ride_id = ride.id
                          AND active_offer.status = 'offered'
                          AND active_offer.expires_at > now()
                    ) THEN 'offered'
                    WHEN ride.status = 'requested' THEN 'pending'
                    ELSE ride.status
                END AS dispatch_status
            FROM public.rides AS ride
            LEFT JOIN public.vehicles AS vehicle
              ON vehicle.id = ride.assigned_vehicle_id
            WHERE ride.id = :ride_id
              {access_filter}
            """
        ),
        params,
    ).mappings().first()
    if row is None:
        return None

    geometry = row["route_geometry"]
    return RideDetailsResponse(
        ride_id=row["ride_id"],
        status=row["status"],
        pickup={
            "lat": row["pickup_lat"],
            "lng": row["pickup_lng"],
        },
        destination={
            "lat": row["destination_lat"],
            "lng": row["destination_lng"],
        },
        pickup_address=row["pickup_address"],
        destination_address=row["destination_address"],
        estimated_distance_m=row["estimated_distance_m"],
        estimated_duration_s=row["estimated_duration_s"],
        estimated_fare=row["estimated_fare"],
        final_distance_m=row["final_distance_m"],
        final_fare=row["final_fare"],
        currency=row["currency"],
        fare_breakdown=row["fare_breakdown"] or {},
        route_geometry=(
            GeoJSONLineString.model_validate(geometry) if geometry else None
        ),
        assigned_driver_id=row["assigned_driver_id"],
        assigned_vehicle=(
            RideVehicleSummary(
                id=row["vehicle_id"],
                vehicle_type=row["vehicle_type"],
            )
            if row["vehicle_id"]
            else None
        ),
        requested_at=row["requested_at"],
        accepted_at=row["accepted_at"],
        arrived_at=row["arrived_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        cancelled_at=row["cancelled_at"],
        dispatch_status=row["dispatch_status"],
    )


@router.post(
    "",
    response_model=RideDetailsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ride(
    request: RideCreateRequest,
    response: Response,
    current_user: AuthenticatedUser = Depends(require_roles("passenger")),
    db: Session = Depends(get_db),
) -> RideDetailsResponse:
    existing = db.execute(
        text(
            """
            SELECT id
            FROM public.rides
            WHERE passenger_id = :passenger_id
              AND client_request_id = :client_request_id
            """
        ),
        {
            "passenger_id": current_user.id,
            "client_request_id": request.client_request_id,
        },
    ).first()
    if existing is not None:
        db.rollback()
        ride = _read_ride(
            db,
            existing["id"],
            passenger_id=current_user.id,
        )
        if ride is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ride not found.",
            )
        response.status_code = status.HTTP_200_OK
        return ride

    db.rollback()
    route = await get_driving_route(request.pickup, request.destination)
    fare = calculate_fare(route.distance_m, route.duration_s)
    try:
        inserted = db.execute(
            text(
                """
                INSERT INTO public.rides (
                    passenger_id, client_request_id, status,
                    pickup_location, pickup_address,
                    destination_location, destination_address,
                    estimated_distance_m, estimated_duration_s,
                    estimated_fare, currency, fare_breakdown, route_geom,
                    requested_at, created_at, updated_at
                )
                VALUES (
                    :passenger_id, :client_request_id, 'requested',
                    ST_SetSRID(ST_MakePoint(:pickup_lng, :pickup_lat), 4326)::geography,
                    :pickup_address,
                    ST_SetSRID(
                        ST_MakePoint(:destination_lng, :destination_lat), 4326
                    )::geography,
                    :destination_address,
                    :estimated_distance_m, :estimated_duration_s,
                    :estimated_fare, 'INR',
                    CAST(:fare_breakdown AS JSONB),
                    ST_SetSRID(
                        ST_GeomFromGeoJSON(:route_geometry), 4326
                    )::geometry(LineString, 4326),
                    now(), now(), now()
                )
                ON CONFLICT (passenger_id, client_request_id)
                    WHERE client_request_id IS NOT NULL
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "passenger_id": current_user.id,
                "client_request_id": request.client_request_id,
                "pickup_lat": request.pickup.lat,
                "pickup_lng": request.pickup.lng,
                "pickup_address": request.pickup_address,
                "destination_lat": request.destination.lat,
                "destination_lng": request.destination.lng,
                "destination_address": request.destination_address,
                "estimated_distance_m": int(round(route.distance_m)),
                "estimated_duration_s": int(round(route.duration_s)),
                "estimated_fare": fare.estimated_total,
                "fare_breakdown": fare.model_dump_json(),
                "route_geometry": route.geometry.model_dump_json(),
            },
        ).mappings().first()
        if inserted is None:
            db.rollback()
            existing = db.execute(
                text(
                    """
                    SELECT id
                    FROM public.rides
                    WHERE passenger_id = :passenger_id
                      AND client_request_id = :client_request_id
                    """
                ),
                {
                    "passenger_id": current_user.id,
                    "client_request_id": request.client_request_id,
                },
            ).first()
            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ride request could not be created.",
                )
            ride = _read_ride(
                db,
                existing["id"],
                passenger_id=current_user.id,
            )
            if ride is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Ride not found.",
                )
            response.status_code = status.HTTP_200_OK
            return ride

        ride_id = inserted["id"]
        record_ride_event(
            db,
            ride_id=ride_id,
            actor_user_id=current_user.id,
            actor_type="passenger",
            event_type="ride_requested",
            from_status=None,
            to_status="requested",
            metadata={"client_request_id": str(request.client_request_id)},
        )
        dispatch = await dispatch_next_driver(db, ride_id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise

    ride = _read_ride(
        db,
        ride_id,
        passenger_id=current_user.id,
    )
    if ride is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found.",
        )
    return ride.model_copy(update={"dispatch_status": dispatch.status})


@router.post("/estimate", response_model=RideEstimateResponse)
async def estimate_ride(
    request: RideEstimateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("passenger")),
) -> RideEstimateResponse:
    route = await get_driving_route(request.pickup, request.destination)
    fare = calculate_fare(route.distance_m, route.duration_s)

    return RideEstimateResponse(
        pickup=request.pickup,
        destination=request.destination,
        estimated_distance_m=route.distance_m,
        estimated_duration_s=route.duration_s,
        estimated_fare=fare.estimated_total,
        currency=fare.currency,
        fare_breakdown=fare,
        route_geometry=route.geometry,
    )


@router.get("/{ride_id}", response_model=RideDetailsResponse)
def get_ride(
    ride_id: UUID,
    current_user: AuthenticatedUser = Depends(
        require_roles("passenger", "driver")
    ),
    db: Session = Depends(get_db),
) -> RideDetailsResponse:
    ride = _read_ride(db, ride_id, requester_id=current_user.id)
    if ride is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found.",
        )
    return ride
