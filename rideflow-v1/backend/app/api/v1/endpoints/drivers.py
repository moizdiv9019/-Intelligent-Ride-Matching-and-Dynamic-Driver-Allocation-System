from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser, get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.schemas.driver import (
    DriverAvailabilityResponse,
    DriverAvailabilityUpdate,
    DriverLocationResponse,
    DriverLocationUpdate,
    DriverMeResponse,
    DriverOnboardingRequest,
    DriverOnboardingResponse,
    DriverProfileResponse,
    VehicleResponse,
)
from app.schemas.location import NearbyDriverResponse


router = APIRouter(prefix="/drivers", tags=["drivers"])


def _http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


@router.get("/nearby", response_model=list[NearbyDriverResponse])
def get_nearby_drivers(
    lat: float = Query(..., ge=-90, le=90, allow_inf_nan=False),
    lng: float = Query(..., ge=-180, le=180, allow_inf_nan=False),
    radius_m: int = Query(
        settings.driver_search_radius_meters,
        ge=1,
        le=50_000,
    ),
    current_user: AuthenticatedUser = Depends(require_roles("passenger")),
    db: Session = Depends(get_db),
) -> list[NearbyDriverResponse]:
    rows = db.execute(
        text(
            """
            SELECT
                dp.user_id AS driver_id,
                ROUND(ST_Y(dl.location::geometry)::numeric, 3)::double precision AS lat,
                ROUND(ST_X(dl.location::geometry)::numeric, 3)::double precision AS lng,
                active_vehicle.vehicle_type,
                ST_Distance(dl.location, search_point.location) AS distance_m
            FROM public.driver_profiles AS dp
            JOIN public.driver_locations AS dl
              ON dl.driver_user_id = dp.user_id
            JOIN LATERAL (
                SELECT vehicle_type
                FROM public.vehicles
                WHERE driver_user_id = dp.user_id AND is_active = TRUE
                ORDER BY created_at DESC, id
                LIMIT 1
            ) AS active_vehicle ON TRUE
            CROSS JOIN (
                SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                    AS location
            ) AS search_point
            WHERE dp.verification_status = 'verified'
              AND dp.availability_status = 'available'
              AND dl.device_recorded_at >= now() - (
                    :max_age_seconds * INTERVAL '1 second'
              )
              AND dl.received_at >= now() - (
                    :max_age_seconds * INTERVAL '1 second'
              )
              AND ST_DWithin(dl.location, search_point.location, :radius_m)
            ORDER BY distance_m ASC
            LIMIT :result_limit
            """
        ),
        {
            "lat": lat,
            "lng": lng,
            "radius_m": radius_m,
            "max_age_seconds": settings.driver_location_max_age_seconds,
            "result_limit": 50,
        },
    ).mappings().all()
    return [
        NearbyDriverResponse(
            driver_id=row["driver_id"],
            lat=round(float(row["lat"]), 3),
            lng=round(float(row["lng"]), 3),
            vehicle_type=row["vehicle_type"],
            distance_m=row["distance_m"],
        )
        for row in rows
    ]


@router.post(
    "/me/onboarding",
    response_model=DriverOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
)
def onboard_driver(
    onboarding: DriverOnboardingRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DriverOnboardingResponse:
    user_id = current_user.id
    profile_exists = db.execute(
        text("SELECT 1 FROM public.profiles WHERE id = :user_id"),
        {"user_id": user_id},
    ).first()
    if profile_exists is None:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Create a user profile before driver onboarding.",
        )

    driver_profile_row = db.execute(
        text(
            """
            INSERT INTO public.driver_profiles (
                user_id, verification_status, availability_status
            )
            VALUES (:user_id, 'pending', 'offline')
            ON CONFLICT (user_id) DO NOTHING
            RETURNING user_id, verification_status, availability_status,
                      last_became_available_at, last_seen_at, created_at, updated_at
            """
        ),
        {"user_id": user_id},
    ).mappings().first()
    if driver_profile_row is None:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Driver onboarding has already been completed.",
        )

    db.execute(
        text(
            """
            INSERT INTO public.user_roles (user_id, role)
            VALUES (:user_id, 'driver')
            ON CONFLICT (user_id, role) DO NOTHING
            """
        ),
        {"user_id": user_id},
    )

    vehicle_row = db.execute(
        text(
            """
            INSERT INTO public.vehicles (
                driver_user_id, vehicle_type, make_model, registration_number,
                seat_capacity, wheelchair_accessible, is_active
            )
            VALUES (
                :user_id, :vehicle_type, :make_model, :registration_number,
                :seat_capacity, :wheelchair_accessible, TRUE
            )
            ON CONFLICT (registration_number) DO NOTHING
            RETURNING id, driver_user_id, vehicle_type, make_model,
                      registration_number, seat_capacity, wheelchair_accessible,
                      is_active, created_at
            """
        ),
        {
            "user_id": user_id,
            "vehicle_type": onboarding.vehicle_type,
            "make_model": onboarding.make_model,
            "registration_number": onboarding.registration_number,
            "seat_capacity": onboarding.seat_capacity,
            "wheelchair_accessible": onboarding.wheelchair_accessible,
        },
    ).mappings().first()
    if vehicle_row is None:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "A vehicle with this registration number is already registered.",
        )

    db.commit()
    return DriverOnboardingResponse(
        driver_profile=DriverProfileResponse.model_validate(dict(driver_profile_row)),
        vehicle=VehicleResponse.model_validate(dict(vehicle_row)),
    )


@router.get("/me", response_model=DriverMeResponse)
def get_my_driver_profile(
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> DriverMeResponse:
    user_id = current_user.id
    driver_profile_row = db.execute(
        text(
            """
            SELECT user_id, verification_status, availability_status,
                   last_became_available_at, last_seen_at, created_at, updated_at
            FROM public.driver_profiles
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    ).mappings().first()
    if driver_profile_row is None:
        raise _http_error(status.HTTP_404_NOT_FOUND, "Driver profile not found.")

    vehicle_row = db.execute(
        text(
            """
            SELECT id, driver_user_id, vehicle_type, make_model, registration_number,
                   seat_capacity, wheelchair_accessible, is_active, created_at
            FROM public.vehicles
            WHERE driver_user_id = :user_id AND is_active = TRUE
            ORDER BY created_at DESC, id
            LIMIT 1
            """
        ),
        {"user_id": user_id},
    ).mappings().first()
    return DriverMeResponse(
        driver_profile=DriverProfileResponse.model_validate(dict(driver_profile_row)),
        active_vehicle=(
            VehicleResponse.model_validate(dict(vehicle_row)) if vehicle_row else None
        ),
    )


@router.patch(
    "/me/availability",
    response_model=DriverAvailabilityResponse,
)
def update_my_availability(
    update: DriverAvailabilityUpdate,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> DriverAvailabilityResponse:
    user_id = current_user.id
    driver_profile = db.execute(
        text(
            """
            SELECT verification_status, availability_status
            FROM public.driver_profiles
            WHERE user_id = :user_id
            FOR UPDATE
            """
        ),
        {"user_id": user_id},
    ).mappings().first()
    if driver_profile is None:
        db.rollback()
        raise _http_error(status.HTTP_404_NOT_FOUND, "Driver profile not found.")

    if (
        update.availability_status == "available"
        and driver_profile["verification_status"] != "verified"
    ):
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Only verified drivers can become available.",
        )

    if update.availability_status == "offline":
        active_ride = db.execute(
            text(
                """
                SELECT id
                FROM public.rides
                WHERE assigned_driver_id = :user_id
                  AND status IN ('requested', 'accepted', 'arriving', 'ongoing')
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).first()
        if active_ride is not None:
            db.rollback()
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Availability cannot be changed while an active ride is in progress.",
            )

    updated_row = db.execute(
        text(
            """
            UPDATE public.driver_profiles
            SET availability_status = :availability_status,
                last_became_available_at = CASE
                    WHEN :availability_status = 'available' THEN now()
                    ELSE last_became_available_at
                END,
                updated_at = now()
            WHERE user_id = :user_id
            RETURNING user_id, availability_status, last_became_available_at
            """
        ),
        {
            "user_id": user_id,
            "availability_status": update.availability_status,
        },
    ).mappings().one()
    db.commit()
    return DriverAvailabilityResponse.model_validate(dict(updated_row))


@router.post(
    "/me/location",
    response_model=DriverLocationResponse,
)
def update_my_location(
    location: DriverLocationUpdate,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> DriverLocationResponse:
    user_id = current_user.id
    driver_profile = db.execute(
        text(
            """
            SELECT verification_status, availability_status
            FROM public.driver_profiles
            WHERE user_id = :user_id
            FOR UPDATE
            """
        ),
        {"user_id": user_id},
    ).mappings().first()
    if driver_profile is None:
        db.rollback()
        raise _http_error(status.HTTP_404_NOT_FOUND, "Driver profile not found.")
    if (
        driver_profile["verification_status"] != "verified"
        or driver_profile["availability_status"] not in ("available", "busy")
    ):
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Only verified drivers who are available or busy can submit locations.",
        )

    location_row = db.execute(
        text(
            """
            INSERT INTO public.driver_locations (
                driver_user_id, location, accuracy_m, heading_deg, speed_mps,
                device_recorded_at, received_at
            )
            VALUES (
                :user_id,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :accuracy_m, :heading_deg, :speed_mps,
                :device_recorded_at, now()
            )
            ON CONFLICT (driver_user_id) DO UPDATE
            SET location = EXCLUDED.location,
                accuracy_m = EXCLUDED.accuracy_m,
                heading_deg = EXCLUDED.heading_deg,
                speed_mps = EXCLUDED.speed_mps,
                device_recorded_at = EXCLUDED.device_recorded_at,
                received_at = now()
            RETURNING driver_user_id, received_at
            """
        ),
        {
            "user_id": user_id,
            "lng": location.lng,
            "lat": location.lat,
            "accuracy_m": location.accuracy_m,
            "heading_deg": location.heading_deg,
            "speed_mps": location.speed_mps,
            "device_recorded_at": location.device_recorded_at,
        },
    ).mappings().one()
    db.execute(
        text(
            """
            UPDATE public.driver_profiles
            SET last_seen_at = now(), updated_at = now()
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    db.commit()
    return DriverLocationResponse.model_validate(dict(location_row))
