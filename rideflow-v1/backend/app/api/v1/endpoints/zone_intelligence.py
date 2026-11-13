from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser
from app.db.session import get_db
from app.schemas.intelligence import ZoneSummaryResponse
from app.services.zone_service import generate_zone_summary


router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.get("/zones/summary", response_model=list[ZoneSummaryResponse])
def get_zone_summary(
    window_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> list[ZoneSummaryResponse]:
    summaries = generate_zone_summary(
        db,
        window_minutes=window_minutes,
        limit=limit,
    )
    return [
        ZoneSummaryResponse.model_validate(asdict(summary))
        for summary in summaries
    ]
