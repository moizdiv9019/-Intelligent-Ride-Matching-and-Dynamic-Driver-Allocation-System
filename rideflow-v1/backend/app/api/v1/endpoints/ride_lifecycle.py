import base64
import binascii
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser
from app.db.session import get_db
from app.schemas.ride import (
    RideCancellationRequest,
    RideEventResponse,
    RideHistoryItem,
    RideHistoryResponse,
    RideLifecycleResponse,
)
from app.services.dispatch_service import dispatch_next_driver
from app.services.fare_service import calculate_fare
from app.services.ride_event_service import record_ride_event


router = APIRouter(tags=["ride lifecycle"])

HistoryRole = Literal["passenger", "driver"]
_SAFE_EVENT_METADATA_KEYS = {
    "offer_id",
    "client_request_id",
    "final_distance_m",
    "final_duration_s",
    "final_fare",
}
_SAFE_FARE_METADATA_KEYS = {
    "base_fare",
    "distance_fare",
    "time_fare",
    "minimum_fare_adjustment",
    "estimated_total",
    "currency",
}


def _http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _lock_ride(db: Session, ride_id: UUID):
    ride = db.execute(
        text(
            """
            SELECT id, passenger_id, assigned_driver_id, assigned_vehicle_id,
                   status, estimated_distance_m, started_at
            FROM public.rides
            WHERE id = :ride_id
            FOR UPDATE
            """
        ),
        {"ride_id": ride_id},
    ).mappings().first()
    if ride is None:
        raise _http_error(status.HTTP_404_NOT_FOUND, "Ride not found.")
    return ride


def _require_assigned_driver(ride, driver_id: UUID) -> None:
    if ride["assigned_driver_id"] != driver_id:
        raise _http_error(status.HTTP_404_NOT_FOUND, "Ride not found.")


def _release_driver(db: Session, driver_id: UUID) -> str:
    row = db.execute(
        text(
            """
            WITH release_time AS (
                SELECT clock_timestamp() AS released_at
            )
            UPDATE public.driver_profiles AS driver
            SET availability_status = CASE
                    WHEN driver.verification_status = 'verified'
                     AND EXISTS (
                        SELECT 1
                        FROM public.driver_locations AS location
                        WHERE location.driver_user_id = driver.user_id
                          AND location.received_at >= release_time.released_at
                              - (:max_age_seconds * INTERVAL '1 second')
                     )
                    THEN 'available'
                    ELSE 'offline'
                END,
                last_became_available_at = CASE
                    WHEN driver.verification_status = 'verified'
                     AND EXISTS (
                        SELECT 1
                        FROM public.driver_locations AS location
                        WHERE location.driver_user_id = driver.user_id
                          AND location.received_at >= release_time.released_at
                              - (:max_age_seconds * INTERVAL '1 second')
                     )
                    THEN release_time.released_at
                    ELSE driver.last_became_available_at
                END,
                updated_at = release_time.released_at
            FROM release_time
            WHERE driver.user_id = :driver_id
            RETURNING driver.availability_status
            """
        ),
        {
            "driver_id": driver_id,
            "max_age_seconds": settings.driver_location_max_age_seconds,
        },
    ).mappings().first()
    if row is None:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "The assigned driver's profile is unavailable.",
        )
    return row["availability_status"]


def _lifecycle_response(
    ride_id: UUID,
    ride_status: str,
    *,
    assigned_driver_id: UUID | None = None,
    assigned_vehicle_id: UUID | None = None,
    arrived_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    final_distance_m: int | None = None,
    final_fare: float | None = None,
    fare_breakdown: dict[str, Any] | None = None,
    dispatch_status: str | None = None,
) -> RideLifecycleResponse:
    return RideLifecycleResponse(
        ride_id=ride_id,
        status=ride_status,
        assigned_driver_id=assigned_driver_id,
        assigned_vehicle_id=assigned_vehicle_id,
        arrived_at=arrived_at,
        started_at=started_at,
        completed_at=completed_at,
        cancelled_at=cancelled_at,
        final_distance_m=final_distance_m,
        final_fare=final_fare,
        fare_breakdown=fare_breakdown,
        dispatch_status=dispatch_status,
    )


@router.post(
    "/driver/rides/{ride_id}/arrived",
    response_model=RideLifecycleResponse,
)
def mark_driver_arrived(
    ride_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> RideLifecycleResponse:
    try:
        ride = _lock_ride(db, ride_id)
        _require_assigned_driver(ride, current_user.id)
        if ride["status"] != "accepted":
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Only an accepted ride can be marked as arrived.",
            )

        updated = db.execute(
            text(
                """
                UPDATE public.rides
                SET status = 'arriving',
                    arrived_at = clock_timestamp(),
                    updated_at = clock_timestamp()
                WHERE id = :ride_id
                  AND assigned_driver_id = :driver_id
                  AND status = 'accepted'
                RETURNING arrived_at
                """
            ),
            {"ride_id": ride_id, "driver_id": current_user.id},
        ).mappings().first()
        if updated is None:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride is no longer in the accepted state.",
            )

        record_ride_event(
            db,
            ride_id=ride_id,
            actor_user_id=current_user.id,
            actor_type="driver",
            event_type="driver_arrived",
            from_status="accepted",
            to_status="arriving",
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Ride state changed before arrival could be recorded.",
        ) from exc
    except HTTPException:
        db.rollback()
        raise

    return _lifecycle_response(
        ride_id,
        "arriving",
        assigned_driver_id=current_user.id,
        assigned_vehicle_id=ride["assigned_vehicle_id"],
        arrived_at=updated["arrived_at"],
    )


@router.post(
    "/driver/rides/{ride_id}/start",
    response_model=RideLifecycleResponse,
)
def start_ride(
    ride_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> RideLifecycleResponse:
    try:
        ride = _lock_ride(db, ride_id)
        _require_assigned_driver(ride, current_user.id)
        if ride["status"] != "arriving":
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Only an arriving ride can be started.",
            )

        updated = db.execute(
            text(
                """
                UPDATE public.rides
                SET status = 'ongoing',
                    started_at = clock_timestamp(),
                    updated_at = clock_timestamp()
                WHERE id = :ride_id
                  AND assigned_driver_id = :driver_id
                  AND status = 'arriving'
                RETURNING started_at
                """
            ),
            {"ride_id": ride_id, "driver_id": current_user.id},
        ).mappings().first()
        if updated is None:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride is no longer in the arriving state.",
            )

        record_ride_event(
            db,
            ride_id=ride_id,
            actor_user_id=current_user.id,
            actor_type="driver",
            event_type="ride_started",
            from_status="arriving",
            to_status="ongoing",
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Ride state changed before it could be started.",
        ) from exc
    except HTTPException:
        db.rollback()
        raise

    return _lifecycle_response(
        ride_id,
        "ongoing",
        assigned_driver_id=current_user.id,
        assigned_vehicle_id=ride["assigned_vehicle_id"],
        started_at=updated["started_at"],
    )


@router.post(
    "/driver/rides/{ride_id}/complete",
    response_model=RideLifecycleResponse,
)
def complete_ride(
    ride_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> RideLifecycleResponse:
    """Finalize using estimated route distance; V1 does not track actual trip distance."""
    try:
        ride = _lock_ride(db, ride_id)
        _require_assigned_driver(ride, current_user.id)
        if ride["status"] != "ongoing":
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Only an ongoing ride can be completed.",
            )
        if ride["started_at"] is None or ride["estimated_distance_m"] is None:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride start time or estimated route distance is unavailable.",
            )

        completed_at = db.execute(
            text("SELECT clock_timestamp() AS completed_at")
        ).mappings().one()["completed_at"]
        duration_s = (completed_at - ride["started_at"]).total_seconds()
        if duration_s < 0:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride start time is later than the completion time.",
            )

        final_distance_m = int(round(float(ride["estimated_distance_m"])))
        fare = calculate_fare(final_distance_m, duration_s)
        fare_breakdown = fare.model_dump()
        updated = db.execute(
            text(
                """
                UPDATE public.rides
                SET status = 'completed',
                    final_distance_m = :final_distance_m,
                    final_fare = :final_fare,
                    completed_at = :completed_at,
                    fare_breakdown = CAST(:fare_breakdown AS JSONB),
                    updated_at = :completed_at
                WHERE id = :ride_id
                  AND assigned_driver_id = :driver_id
                  AND status = 'ongoing'
                RETURNING final_distance_m, final_fare, fare_breakdown,
                          completed_at
                """
            ),
            {
                "ride_id": ride_id,
                "driver_id": current_user.id,
                "final_distance_m": final_distance_m,
                "final_fare": fare.estimated_total,
                "completed_at": completed_at,
                "fare_breakdown": fare.model_dump_json(),
            },
        ).mappings().first()
        if updated is None:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride is no longer in the ongoing state.",
            )

        _release_driver(db, current_user.id)
        record_ride_event(
            db,
            ride_id=ride_id,
            actor_user_id=current_user.id,
            actor_type="driver",
            event_type="ride_completed",
            from_status="ongoing",
            to_status="completed",
            metadata={
                "final_distance_m": final_distance_m,
                "final_duration_s": round(duration_s, 2),
                "final_fare": fare.estimated_total,
                "fare_breakdown": fare_breakdown,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Ride state changed before it could be completed.",
        ) from exc
    except HTTPException:
        db.rollback()
        raise

    return _lifecycle_response(
        ride_id,
        "completed",
        assigned_driver_id=current_user.id,
        assigned_vehicle_id=ride["assigned_vehicle_id"],
        completed_at=updated["completed_at"],
        final_distance_m=updated["final_distance_m"],
        final_fare=float(updated["final_fare"]),
        fare_breakdown=updated["fare_breakdown"],
    )


@router.post(
    "/rides/{ride_id}/cancel",
    response_model=RideLifecycleResponse,
)
def cancel_ride_by_passenger(
    ride_id: UUID,
    cancellation: RideCancellationRequest | None = None,
    current_user: AuthenticatedUser = Depends(require_roles("passenger")),
    db: Session = Depends(get_db),
) -> RideLifecycleResponse:
    try:
        ride = _lock_ride(db, ride_id)
        if ride["passenger_id"] != current_user.id:
            raise _http_error(status.HTTP_404_NOT_FOUND, "Ride not found.")
        if ride["status"] not in ("requested", "accepted", "arriving"):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride can no longer be cancelled by the passenger.",
            )

        reason = (
            (cancellation.reason or "").strip() or None if cancellation else None
        )
        updated = db.execute(
            text(
                """
                UPDATE public.rides
                SET status = 'cancelled',
                    cancelled_at = clock_timestamp(),
                    cancelled_by = :passenger_id,
                    cancellation_reason = :reason,
                    updated_at = clock_timestamp()
                WHERE id = :ride_id
                  AND passenger_id = :passenger_id
                  AND status = :previous_status
                RETURNING cancelled_at
                """
            ),
            {
                "ride_id": ride_id,
                "passenger_id": current_user.id,
                "previous_status": ride["status"],
                "reason": reason,
            },
        ).mappings().first()
        if updated is None:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride state changed before cancellation could be recorded.",
            )

        db.execute(
            text(
                """
                UPDATE public.ride_offers
                SET status = 'withdrawn', responded_at = clock_timestamp()
                WHERE ride_id = :ride_id AND status = 'offered'
                """
            ),
            {"ride_id": ride_id},
        )
        if ride["assigned_driver_id"] is not None:
            _release_driver(db, ride["assigned_driver_id"])

        record_ride_event(
            db,
            ride_id=ride_id,
            actor_user_id=current_user.id,
            actor_type="passenger",
            event_type="ride_cancelled_by_passenger",
            from_status=ride["status"],
            to_status="cancelled",
            metadata={"reason": reason} if reason else None,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Ride state changed before passenger cancellation could finish.",
        ) from exc
    except HTTPException:
        db.rollback()
        raise

    return _lifecycle_response(
        ride_id,
        "cancelled",
        assigned_driver_id=ride["assigned_driver_id"],
        assigned_vehicle_id=ride["assigned_vehicle_id"],
        cancelled_at=updated["cancelled_at"],
    )


@router.post(
    "/driver/rides/{ride_id}/cancel",
    response_model=RideLifecycleResponse,
)
async def cancel_ride_by_driver(
    ride_id: UUID,
    cancellation: RideCancellationRequest | None = None,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> RideLifecycleResponse:
    try:
        ride = _lock_ride(db, ride_id)
        _require_assigned_driver(ride, current_user.id)
        if ride["status"] not in ("accepted", "arriving"):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "A driver can cancel only before the ride starts.",
            )

        reason = (
            (cancellation.reason or "").strip() or None if cancellation else None
        )
        updated = db.execute(
            text(
                """
                UPDATE public.rides
                SET status = 'requested',
                    assigned_driver_id = NULL,
                    assigned_vehicle_id = NULL,
                    updated_at = clock_timestamp()
                WHERE id = :ride_id
                  AND assigned_driver_id = :driver_id
                  AND status = :previous_status
                RETURNING id
                """
            ),
            {
                "ride_id": ride_id,
                "driver_id": current_user.id,
                "previous_status": ride["status"],
            },
        ).mappings().first()
        if updated is None:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "Ride state changed before driver cancellation could finish.",
            )

        db.execute(
            text(
                """
                UPDATE public.ride_offers
                SET status = 'withdrawn', responded_at = clock_timestamp()
                WHERE ride_id = :ride_id
                  AND driver_user_id = :driver_id
                  AND status = 'accepted'
                """
            ),
            {"ride_id": ride_id, "driver_id": current_user.id},
        )
        _release_driver(db, current_user.id)
        record_ride_event(
            db,
            ride_id=ride_id,
            actor_user_id=current_user.id,
            actor_type="driver",
            event_type="ride_cancelled_by_driver",
            from_status=ride["status"],
            to_status="requested",
            metadata={"reason": reason} if reason else None,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _http_error(
            status.HTTP_409_CONFLICT,
            "Ride state changed before driver cancellation could finish.",
        ) from exc
    except HTTPException:
        db.rollback()
        raise

    dispatch = await dispatch_next_driver(db, ride_id)
    db.commit()
    return _lifecycle_response(
        ride_id,
        "no_driver_found" if dispatch.status == "no_driver_found" else "requested",
        dispatch_status=dispatch.status,
    )


def _encode_cursor(created_at: datetime, ride_id: UUID) -> str:
    value = json.dumps(
        {"created_at": created_at.isoformat(), "ride_id": str(ride_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        decoded = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        value = json.loads(decoded)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("created_at"), str)
            or not isinstance(value.get("ride_id"), str)
        ):
            raise ValueError("Cursor fields have invalid types.")
        created_at = datetime.fromisoformat(value["created_at"])
        ride_id = UUID(value["ride_id"])
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("Cursor timestamp must include a timezone.")
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise _http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid cursor.") from exc
    return created_at, ride_id


@router.get("/me/rides", response_model=RideHistoryResponse)
def get_my_ride_history(
    role: HistoryRole = Query("passenger"),
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None, min_length=1, max_length=512),
    current_user: AuthenticatedUser = Depends(
        require_roles("passenger", "driver")
    ),
    db: Session = Depends(get_db),
) -> RideHistoryResponse:
    selected_role = db.execute(
        text(
            """
            SELECT 1
            FROM public.user_roles
            WHERE user_id = :user_id AND role = :role
            """
        ),
        {"user_id": current_user.id, "role": role},
    ).first()
    if selected_role is None:
        raise _http_error(status.HTTP_403_FORBIDDEN, "Insufficient role.")

    owner_column = "passenger_id" if role == "passenger" else "assigned_driver_id"
    cursor_filter = ""
    params: dict[str, Any] = {
        "user_id": current_user.id,
        "fetch_limit": limit + 1,
    }
    if cursor:
        cursor_created_at, cursor_ride_id = _decode_cursor(cursor)
        cursor_filter = "AND (created_at, id) < (:cursor_created_at, :cursor_ride_id)"
        params["cursor_created_at"] = cursor_created_at
        params["cursor_ride_id"] = cursor_ride_id

    rows = db.execute(
        text(
            f"""
            SELECT id AS ride_id, status, pickup_address, destination_address,
                   estimated_fare, final_fare, created_at, requested_at,
                   accepted_at, arrived_at, started_at, completed_at, cancelled_at
            FROM public.rides
            WHERE {owner_column} = :user_id
              {cursor_filter}
            ORDER BY created_at DESC, id DESC
            LIMIT :fetch_limit
            """
        ),
        params,
    ).mappings().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        _encode_cursor(page[-1]["created_at"], page[-1]["ride_id"])
        if has_more and page
        else None
    )
    return RideHistoryResponse(
        items=[RideHistoryItem.model_validate(dict(row)) for row in page],
        next_cursor=next_cursor,
    )


def _safe_event_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    safe = {
        key: metadata[key]
        for key in _SAFE_EVENT_METADATA_KEYS
        if key in metadata
        and isinstance(metadata[key], (str, int, float, bool, type(None)))
    }
    fare_breakdown = metadata.get("fare_breakdown")
    if isinstance(fare_breakdown, dict):
        safe["fare_breakdown"] = {
            key: fare_breakdown[key]
            for key in _SAFE_FARE_METADATA_KEYS
            if key in fare_breakdown
            and isinstance(
                fare_breakdown[key],
                (str, int, float, bool, type(None)),
            )
        }
    return safe


@router.get(
    "/rides/{ride_id}/events",
    response_model=list[RideEventResponse],
)
def get_ride_events(
    ride_id: UUID,
    current_user: AuthenticatedUser = Depends(
        require_roles("passenger", "driver")
    ),
    db: Session = Depends(get_db),
) -> list[RideEventResponse]:
    access = db.execute(
        text(
            """
            SELECT id
            FROM public.rides
            WHERE id = :ride_id
              AND (
                    passenger_id = :user_id
                    OR assigned_driver_id = :user_id
              )
            """
        ),
        {"ride_id": ride_id, "user_id": current_user.id},
    ).first()
    if access is None:
        raise _http_error(status.HTTP_404_NOT_FOUND, "Ride not found.")

    rows = db.execute(
        text(
            """
            SELECT id AS event_id, event_type, actor_type, created_at,
                   from_status, to_status, metadata
            FROM public.ride_events
            WHERE ride_id = :ride_id
            ORDER BY created_at ASC, id ASC
            """
        ),
        {"ride_id": ride_id},
    ).mappings().all()
    return [
        RideEventResponse(
            event_id=row["event_id"],
            event_type=row["event_type"],
            actor_type=row["actor_type"],
            created_at=row["created_at"],
            from_status=row["from_status"],
            to_status=row["to_status"],
            metadata=_safe_event_metadata(row["metadata"]),
        )
        for row in rows
    ]
