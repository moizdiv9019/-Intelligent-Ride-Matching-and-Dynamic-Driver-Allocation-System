from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser
from app.db.session import get_db
from app.schemas.location import Coordinate
from app.schemas.ride import (
    DriverOfferResponse,
    OfferActionResponse,
    OfferRejectRequest,
)
from app.services.dispatch_service import dispatch_next_driver
from app.services.ride_event_service import record_ride_event


router = APIRouter(tags=["driver offers"])


@router.get(
    "/drivers/me/offers",
    response_model=list[DriverOfferResponse],
)
def get_my_offers(
    offer_status: Literal["offered"] = Query("offered", alias="status"),
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> list[DriverOfferResponse]:
    rows = db.execute(
        text(
            """
            SELECT
                offer.id AS offer_id,
                ride.id AS ride_id,
                ride.pickup_address,
                ride.destination_address,
                ROUND(
                    ST_Y(ride.pickup_location::geometry)::numeric, 3
                )::double precision AS pickup_lat,
                ROUND(
                    ST_X(ride.pickup_location::geometry)::numeric, 3
                )::double precision AS pickup_lng,
                offer.pickup_distance_m,
                offer.pickup_eta_s,
                ride.estimated_fare,
                offer.expires_at
            FROM public.ride_offers AS offer
            JOIN public.rides AS ride ON ride.id = offer.ride_id
            WHERE offer.driver_user_id = :driver_user_id
              AND offer.status = :offer_status
              AND offer.expires_at > now()
              AND ride.status = 'requested'
              AND ride.assigned_driver_id IS NULL
            ORDER BY offer.offered_at
            """
        ),
        {
            "driver_user_id": current_user.id,
            "offer_status": offer_status,
        },
    ).mappings().all()
    return [
        DriverOfferResponse(
            offer_id=row["offer_id"],
            ride_id=row["ride_id"],
            pickup_address=row["pickup_address"],
            destination_address=row["destination_address"],
            pickup=Coordinate(
                lat=round(float(row["pickup_lat"]), 3),
                lng=round(float(row["pickup_lng"]), 3),
            ),
            pickup_distance_m=row["pickup_distance_m"],
            pickup_eta_s=row["pickup_eta_s"],
            estimated_fare=row["estimated_fare"],
            expires_at=row["expires_at"],
        )
        for row in rows
    ]


def _lock_owned_offer(
    db: Session,
    *,
    offer_id: UUID,
    driver_user_id: UUID,
):
    row = db.execute(
        text(
            """
            SELECT id, ride_id
            FROM public.ride_offers
            WHERE id = :offer_id AND driver_user_id = :driver_user_id
            """
        ),
        {"offer_id": offer_id, "driver_user_id": driver_user_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Offer not found.",
        )

    offer = db.execute(
        text(
            """
            SELECT id, ride_id, driver_user_id, status,
                   expires_at <= now() AS is_expired
            FROM public.ride_offers
            WHERE id = :offer_id AND driver_user_id = :driver_user_id
            FOR UPDATE
            """
        ),
        {"offer_id": offer_id, "driver_user_id": driver_user_id},
    ).mappings().first()
    if offer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Offer not found.",
        )

    ride = db.execute(
        text(
            """
            SELECT id, status, assigned_driver_id
            FROM public.rides
            WHERE id = :ride_id
            FOR UPDATE
            """
        ),
        {"ride_id": offer["ride_id"]},
    ).mappings().first()
    if ride is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found.",
        )
    return offer, ride


def _expire_locked_offer(db: Session, offer) -> bool:
    expired = db.execute(
        text(
            """
            UPDATE public.ride_offers
            SET status = 'expired', responded_at = now()
            WHERE id = :offer_id
              AND status = 'offered'
              AND expires_at <= now()
            RETURNING id
            """
        ),
        {"offer_id": offer["id"]},
    ).first()
    if expired is None:
        return False
    record_ride_event(
        db,
        ride_id=offer["ride_id"],
        actor_user_id=None,
        actor_type="system",
        event_type="driver_offer_expired",
        from_status="offered",
        to_status="expired",
        metadata={"offer_id": str(offer["id"])},
    )
    return True


@router.post(
    "/driver/offers/{offer_id}/accept",
    response_model=OfferActionResponse,
)
def accept_offer(
    offer_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    try:
        offer, ride = _lock_owned_offer(
            db,
            offer_id=offer_id,
            driver_user_id=current_user.id,
        )
        if offer["status"] != "offered":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Offer is no longer available.",
            )
        if offer["is_expired"]:
            _expire_locked_offer(db, offer)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Offer has expired.",
            )
        if ride["status"] != "requested" or ride["assigned_driver_id"] is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ride is no longer available.",
            )

        driver = db.execute(
            text(
                """
                SELECT verification_status, availability_status
                FROM public.driver_profiles
                WHERE user_id = :driver_user_id
                FOR UPDATE
                """
            ),
            {"driver_user_id": current_user.id},
        ).mappings().first()
        if (
            driver is None
            or driver["verification_status"] != "verified"
            or driver["availability_status"] != "available"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Driver is no longer available for this ride.",
            )

        active_ride = db.execute(
            text(
                """
                SELECT id
                FROM public.rides
                WHERE assigned_driver_id = :driver_user_id
                  AND id <> :ride_id
                  AND status IN ('accepted', 'arriving', 'ongoing')
                LIMIT 1
                """
            ),
            {
                "driver_user_id": current_user.id,
                "ride_id": offer["ride_id"],
            },
        ).first()
        if active_ride is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Driver already has an active ride.",
            )

        vehicle = db.execute(
            text(
                """
                SELECT id
                FROM public.vehicles
                WHERE driver_user_id = :driver_user_id AND is_active = TRUE
                ORDER BY id
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"driver_user_id": current_user.id},
        ).mappings().first()
        if vehicle is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Driver has no active vehicle.",
            )

        updated_offer = db.execute(
            text(
                """
                UPDATE public.ride_offers
                SET status = 'accepted', responded_at = now()
                WHERE id = :offer_id
                  AND status = 'offered'
                  AND expires_at > now()
                RETURNING id
                """
            ),
            {"offer_id": offer_id},
        ).first()
        if updated_offer is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Offer is no longer available.",
            )

        assigned_ride = db.execute(
            text(
                """
                UPDATE public.rides
                SET assigned_driver_id = :driver_user_id,
                    assigned_vehicle_id = :vehicle_id,
                    status = 'accepted',
                    accepted_at = now(),
                    updated_at = now()
                WHERE id = :ride_id
                  AND status = 'requested'
                  AND assigned_driver_id IS NULL
                RETURNING id
                """
            ),
            {
                "ride_id": offer["ride_id"],
                "driver_user_id": current_user.id,
                "vehicle_id": vehicle["id"],
            },
        ).first()
        if assigned_ride is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ride is no longer available.",
            )

        db.execute(
            text(
                """
                UPDATE public.driver_profiles
                SET availability_status = 'busy', updated_at = now()
                WHERE user_id = :driver_user_id
                """
            ),
            {"driver_user_id": current_user.id},
        )
        db.execute(
            text(
                """
                UPDATE public.ride_offers
                SET status = 'withdrawn', responded_at = now()
                WHERE ride_id = :ride_id
                  AND id <> :offer_id
                  AND status = 'offered'
                """
            ),
            {"ride_id": offer["ride_id"], "offer_id": offer_id},
        )
        record_ride_event(
            db,
            ride_id=offer["ride_id"],
            actor_user_id=current_user.id,
            actor_type="driver",
            event_type="driver_offer_accepted",
            from_status="requested",
            to_status="accepted",
            metadata={"offer_id": str(offer_id)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ride or driver is no longer available.",
        ) from exc
    except HTTPException:
        if db.in_transaction():
            db.rollback()
        raise

    return OfferActionResponse(
        offer_id=offer_id,
        ride_id=offer["ride_id"],
        status="accepted",
    )


@router.post(
    "/driver/offers/{offer_id}/reject",
    response_model=OfferActionResponse,
)
async def reject_offer(
    offer_id: UUID,
    rejection: OfferRejectRequest | None = None,
    current_user: AuthenticatedUser = Depends(require_roles("driver")),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    try:
        offer, ride = _lock_owned_offer(
            db,
            offer_id=offer_id,
            driver_user_id=current_user.id,
        )
        if offer["status"] != "offered" or offer["is_expired"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Offer is no longer available.",
            )
        if ride["status"] != "requested" or ride["assigned_driver_id"] is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ride is no longer available.",
            )

        db.execute(
            text(
                """
                UPDATE public.ride_offers
                SET status = 'rejected', responded_at = now()
                WHERE id = :offer_id AND status = 'offered'
                """
            ),
            {"offer_id": offer_id},
        )
        record_ride_event(
            db,
            ride_id=offer["ride_id"],
            actor_user_id=current_user.id,
            actor_type="driver",
            event_type="driver_offer_rejected",
            from_status="offered",
            to_status="rejected",
            metadata={
                "offer_id": str(offer_id),
                "reason": rejection.reason if rejection else None,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Offer is no longer available.",
        ) from exc
    except HTTPException:
        if db.in_transaction():
            db.rollback()
        raise

    dispatch = await dispatch_next_driver(db, offer["ride_id"])
    db.commit()
    return OfferActionResponse(
        offer_id=offer_id,
        ride_id=offer["ride_id"],
        status="rejected",
        dispatch_status=dispatch.status,
    )
