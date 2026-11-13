import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def record_ride_event(
    db: Session,
    *,
    ride_id: UUID,
    actor_user_id: UUID | None,
    actor_type: str,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    metadata: dict | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO public.ride_events (
                ride_id, actor_user_id, actor_type, event_type,
                from_status, to_status, metadata
            )
            VALUES (
                :ride_id, :actor_user_id, :actor_type, :event_type,
                :from_status, :to_status, CAST(:metadata AS JSONB)
            )
            """
        ),
        {
            "ride_id": ride_id,
            "actor_user_id": actor_user_id,
            "actor_type": actor_type,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "metadata": json.dumps(metadata or {}),
        },
    )
