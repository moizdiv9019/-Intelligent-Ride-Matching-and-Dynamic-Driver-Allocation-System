from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser
from app.db.session import get_db
from app.schemas.ride import PoolingPreviewResponse
from app.services.pooling_service import preview_pool


router = APIRouter(prefix="/rides", tags=["ride-pooling"])


@router.post(
    "/{ride_id}/pooling/preview",
    response_model=PoolingPreviewResponse,
)
async def preview_ride_pooling(
    ride_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("passenger")),
    db: Session = Depends(get_db),
) -> PoolingPreviewResponse:
    return await preview_pool(
        db,
        ride_id=ride_id,
        passenger_id=current_user.id,
    )
