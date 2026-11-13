import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.location import Coordinate
from app.services.ride_event_service import record_ride_event
from app.services.routing_service import get_driving_route


@dataclass(frozen=True)
class DispatchResult:
    status: str
    offer_id: UUID | None = None
    driver_user_id: UUID | None = None
    pickup_eta_s: int | None = None


def calculate_match_score(
    eta_seconds: float,
    pickup_distance_m: float,
    idle_seconds: float,
) -> dict[str, Any]:
    eta_score = max(
        0.0,
        min(
            100.0,
            100.0
            * (1.0 - eta_seconds / settings.matching_eta_max_seconds),
        ),
    )
    distance_score = max(
        0.0,
        min(
            100.0,
            100.0
            * (
                1.0
                - pickup_distance_m / settings.matching_distance_max_meters
            ),
        ),
    )
    idle_score = max(
        0.0,
        min(
            100.0,
            100.0 * idle_seconds / settings.matching_idle_max_seconds,
        ),
    )
    weights = {
        "eta": settings.matching_eta_weight,
        "distance": settings.matching_distance_weight,
        "idle_time": settings.matching_idle_time_weight,
    }
    final_score = (
        eta_score * weights["eta"]
        + distance_score * weights["distance"]
        + idle_score * weights["idle_time"]
    )
    return {
        "eta_seconds": eta_seconds,
        "pickup_distance_m": pickup_distance_m,
        "idle_seconds": idle_seconds,
        "eta_score": eta_score,
        "distance_score": distance_score,
        "idle_score": idle_score,
        "weights": weights,
        "final_score": final_score,
    }


def _mark_no_driver_found(db: Session, ride_id: UUID) -> DispatchResult:
    updated = db.execute(
        text(
            """
            UPDATE public.rides
            SET status = 'no_driver_found', updated_at = now()
            WHERE id = :ride_id
              AND status = 'requested'
              AND assigned_driver_id IS NULL
            RETURNING id
            """
        ),
        {"ride_id": ride_id},
    ).first()
    if updated is None:
        return DispatchResult(status="not_dispatchable")

    record_ride_event(
        db,
        ride_id=ride_id,
        actor_user_id=None,
        actor_type="system",
        event_type="no_driver_found",
        from_status="requested",
        to_status="no_driver_found",
    )
    return DispatchResult(status="no_driver_found")


async def dispatch_next_driver(db: Session, ride_id: UUID) -> DispatchResult:
    ride = db.execute(
        text(
            """
            SELECT status, assigned_driver_id,
                   ST_X(pickup_location::geometry) AS pickup_lng,
                   ST_Y(pickup_location::geometry) AS pickup_lat
            FROM public.rides
            WHERE id = :ride_id
            FOR UPDATE
            """
        ),
        {"ride_id": ride_id},
    ).mappings().first()
    if ride is None:
        return DispatchResult(status="ride_not_found")
    if ride["status"] != "requested" or ride["assigned_driver_id"] is not None:
        return DispatchResult(status="not_dispatchable")

    current_offer = db.execute(
        text(
            """
            SELECT id, driver_user_id, expires_at > now() AS is_active
            FROM public.ride_offers
            WHERE ride_id = :ride_id AND status = 'offered'
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"ride_id": ride_id},
    ).mappings().first()
    if current_offer is not None:
        return DispatchResult(
            status="offered" if current_offer["is_active"] else "pending",
            offer_id=current_offer["id"],
            driver_user_id=current_offer["driver_user_id"],
        )

    candidates = db.execute(
        text(
            """
            SELECT
                dp.user_id AS driver_user_id,
                active_vehicle.id AS vehicle_id,
                ST_X(dl.location::geometry) AS driver_lng,
                ST_Y(dl.location::geometry) AS driver_lat,
                ST_Distance(dl.location, ride.pickup_location) AS pickup_distance_m,
                EXTRACT(EPOCH FROM (
                    now() - COALESCE(
                        dp.last_became_available_at,
                        dp.last_seen_at,
                        now()
                    )
                )) AS idle_seconds
            FROM public.rides AS ride
            JOIN public.driver_profiles AS dp
              ON dp.verification_status = 'verified'
             AND dp.availability_status = 'available'
            JOIN public.driver_locations AS dl
              ON dl.driver_user_id = dp.user_id
            JOIN LATERAL (
                SELECT id
                FROM public.vehicles
                WHERE driver_user_id = dp.user_id AND is_active = TRUE
                ORDER BY id
                LIMIT 1
            ) AS active_vehicle ON TRUE
            WHERE ride.id = :ride_id
              AND ride.status = 'requested'
              AND ride.assigned_driver_id IS NULL
              AND dl.received_at >= now() - (
                    :max_age_seconds * INTERVAL '1 second'
              )
              AND ST_DWithin(
                    dl.location,
                    ride.pickup_location,
                    :search_radius_m
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM public.ride_offers AS previous_offer
                    WHERE previous_offer.ride_id = ride.id
                      AND previous_offer.driver_user_id = dp.user_id
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM public.ride_offers AS active_offer
                    WHERE active_offer.driver_user_id = dp.user_id
                      AND active_offer.status = 'offered'
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM public.rides AS conflicting_ride
                    WHERE conflicting_ride.assigned_driver_id = dp.user_id
                      AND conflicting_ride.status IN (
                            'accepted', 'arriving', 'ongoing'
                      )
              )
            ORDER BY pickup_distance_m ASC, dp.user_id ASC
            LIMIT 10
            FOR UPDATE OF dp SKIP LOCKED
            """
        ),
        {
            "ride_id": ride_id,
            "max_age_seconds": settings.driver_location_max_age_seconds,
            "search_radius_m": settings.driver_search_radius_meters,
        },
    ).mappings().all()
    if not candidates:
        return _mark_no_driver_found(db, ride_id)

    ranked_candidates = []
    routing_unavailable = False
    pickup = Coordinate(lat=ride["pickup_lat"], lng=ride["pickup_lng"])
    for candidate in candidates:
        try:
            route = await get_driving_route(
                pickup,
                Coordinate(
                    lat=candidate["driver_lat"],
                    lng=candidate["driver_lng"],
                ),
            )
        except HTTPException as exc:
            if exc.status_code == 422:
                continue
            if exc.status_code == 503:
                routing_unavailable = True
                continue
            raise

        eta_seconds = float(route.duration_s)
        pickup_distance_m = float(candidate["pickup_distance_m"])
        idle_seconds = float(candidate["idle_seconds"] or 0)
        match_factors = calculate_match_score(
            eta_seconds,
            pickup_distance_m,
            idle_seconds,
        )
        ranked_candidates.append(
            (
                match_factors["final_score"],
                eta_seconds,
                pickup_distance_m,
                idle_seconds,
                candidate,
                match_factors,
            )
        )

    ranked_candidates.sort(
        key=lambda result: (
            -result[0],
            result[1],
            result[2],
            -result[3],
            str(result[4]["driver_user_id"]),
        )
    )
    if not ranked_candidates:
        if routing_unavailable:
            return DispatchResult(status="pending")
        return _mark_no_driver_found(db, ride_id)

    next_offer_number = db.execute(
        text(
            """
            SELECT COALESCE(MAX(offer_number), 0) + 1
            FROM public.ride_offers
            WHERE ride_id = :ride_id
            """
        ),
        {"ride_id": ride_id},
    ).scalar_one()

    for (
        match_score,
        eta_seconds,
        pickup_distance_m_raw,
        _,
        candidate,
        match_factors,
    ) in ranked_candidates:
        pickup_distance_m = int(round(pickup_distance_m_raw))
        pickup_eta_s = int(round(eta_seconds))
        offer = db.execute(
            text(
                """
                INSERT INTO public.ride_offers (
                    ride_id, driver_user_id, offer_number, status,
                    pickup_distance_m, pickup_eta_s, match_score,
                    match_factors, offered_at, expires_at
                )
                VALUES (
                    :ride_id, :driver_user_id, :offer_number, 'offered',
                    :pickup_distance_m, :pickup_eta_s, :match_score,
                    CAST(:match_factors AS JSONB), now(),
                    now() + (:timeout_seconds * INTERVAL '1 second')
                )
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {
                "ride_id": ride_id,
                "driver_user_id": candidate["driver_user_id"],
                "offer_number": next_offer_number,
                "pickup_distance_m": pickup_distance_m,
                "pickup_eta_s": pickup_eta_s,
                "match_score": match_score,
                "match_factors": json.dumps(match_factors),
                "timeout_seconds": settings.offer_timeout_seconds,
            },
        ).mappings().first()
        if offer is None:
            continue

        previous_driver_cancellation = db.execute(
            text(
                """
                SELECT actor_user_id
                FROM public.ride_events
                WHERE ride_id = :ride_id
                  AND event_type = 'ride_cancelled_by_driver'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"ride_id": ride_id},
        ).mappings().first()
        record_ride_event(
            db,
            ride_id=ride_id,
            actor_user_id=None,
            actor_type="system",
            event_type="driver_offer_created",
            from_status="requested",
            to_status="requested",
            metadata={
                "offer_id": str(offer["id"]),
                "driver_user_id": str(candidate["driver_user_id"]),
            },
        )
        if (
            previous_driver_cancellation is not None
            and previous_driver_cancellation["actor_user_id"]
            != candidate["driver_user_id"]
        ):
            record_ride_event(
                db,
                ride_id=ride_id,
                actor_user_id=None,
                actor_type="system",
                event_type="driver_reassigned",
                from_status="requested",
                to_status="requested",
                metadata={
                    "previous_driver_id": str(
                        previous_driver_cancellation["actor_user_id"]
                    ),
                    "new_driver_id": str(candidate["driver_user_id"]),
                    "offer_id": str(offer["id"]),
                },
            )
        return DispatchResult(
            status="offered",
            offer_id=offer["id"],
            driver_user_id=candidate["driver_user_id"],
            pickup_eta_s=pickup_eta_s,
        )

    current_offer = db.execute(
        text(
            """
            SELECT id, driver_user_id
            FROM public.ride_offers
            WHERE ride_id = :ride_id AND status = 'offered'
            LIMIT 1
            """
        ),
        {"ride_id": ride_id},
    ).mappings().first()
    if current_offer is not None:
        return DispatchResult(
            status="offered",
            offer_id=current_offer["id"],
            driver_user_id=current_offer["driver_user_id"],
        )
    if routing_unavailable:
        return DispatchResult(status="pending")
    return _mark_no_driver_found(db, ride_id)
