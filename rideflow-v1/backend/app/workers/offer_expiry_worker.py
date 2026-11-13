import asyncio
import logging
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services.dispatch_service import dispatch_next_driver
from app.services.ride_event_service import record_ride_event


logger = logging.getLogger(__name__)


def _expire_due_offers(db: Session, batch_size: int = 100) -> list[UUID]:
    offers = db.execute(
        text(
            """
            SELECT id, ride_id
            FROM public.ride_offers
            WHERE status = 'offered' AND expires_at <= now()
            ORDER BY expires_at
            LIMIT :batch_size
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"batch_size": batch_size},
    ).mappings().all()

    ride_ids: list[UUID] = []
    for offer in offers:
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
            continue

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
        ride_ids.append(offer["ride_id"])

    return ride_ids


async def process_offer_expiry_once(
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    with session_factory() as db:
        ride_ids = set(_expire_due_offers(db))
        db.commit()

        pending_rides = db.execute(
            text(
                """
                SELECT ride.id
                FROM public.rides AS ride
                WHERE ride.status = 'requested'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.ride_offers AS offer
                      WHERE offer.ride_id = ride.id
                        AND offer.status = 'offered'
                  )
                ORDER BY ride.requested_at
                LIMIT 100
                """
            )
        ).scalars().all()
        ride_ids.update(pending_rides)
        db.rollback()

        for ride_id in ride_ids:
            try:
                await dispatch_next_driver(db, ride_id)
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.error(
                    "Offer expiry worker dispatch failed (%s); it will retry.",
                    type(exc).__name__,
                )

        return len(ride_ids)


async def run_offer_expiry_worker(
    stop_event: asyncio.Event,
    session_factory: Callable[[], Session] | None = None,
    poll_interval_seconds: float | None = None,
) -> None:
    if session_factory is None:
        if engine is None:
            logger.error("Offer expiry worker is disabled because the database is not configured.")
            return
        session_factory = SessionLocal
    interval = (
        poll_interval_seconds
        if poll_interval_seconds is not None
        else settings.offer_expiry_poll_interval_seconds
    )

    while not stop_event.is_set():
        try:
            await process_offer_expiry_once(session_factory)
        except Exception as exc:
            logger.error(
                "Offer expiry worker cycle failed (%s); it will retry.",
                type(exc).__name__,
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue
