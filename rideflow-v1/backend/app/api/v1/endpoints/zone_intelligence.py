from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import require_roles
from app.core.security import AuthenticatedUser
from app.db.session import get_db
from app.schemas.intelligence import (
    DemandForecastEvaluationResponse,
    DemandForecastResponse,
    RepositioningRecommendationsResponse,
    ZoneSummaryResponse,
    ZoneSurgeResponse,
)
from app.services.demand_forecast_service import (
    InsufficientForecastDataError,
    generate_demand_forecast,
    get_forecast_evaluation,
)
from app.services.repositioning_service import (
    generate_repositioning_recommendations,
)
from app.services.surge_pricing_service import generate_surge_analysis
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


@router.get("/zones/forecast", response_model=DemandForecastResponse)
def get_zone_demand_forecast(
    hours_ahead: int = Query(default=1, ge=1, le=6),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: AuthenticatedUser = Depends(require_roles("admin")),
) -> DemandForecastResponse:
    _validate_intelligence_horizon(hours_ahead)
    try:
        forecast = generate_demand_forecast(
            hours_ahead=hours_ahead,
            limit=limit,
        )
    except InsufficientForecastDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return DemandForecastResponse.model_validate(asdict(forecast))


@router.get(
    "/zones/forecast/evaluation",
    response_model=DemandForecastEvaluationResponse,
)
def get_zone_demand_forecast_evaluation(
    _admin: AuthenticatedUser = Depends(require_roles("admin")),
) -> DemandForecastEvaluationResponse:
    try:
        evaluation = get_forecast_evaluation()
    except InsufficientForecastDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return DemandForecastEvaluationResponse.model_validate(asdict(evaluation))


def _validate_intelligence_horizon(hours_ahead: int) -> None:
    if hours_ahead not in (1, 3, 6):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Forecast horizon must be 1, 3, or 6 hours.",
        )
    if hours_ahead > settings.demand_forecast_max_hours_ahead:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Forecast horizon exceeds the configured maximum.",
        )


@router.get("/zones/surge", response_model=ZoneSurgeResponse)
def get_zone_surge_intelligence(
    hours_ahead: int = Query(default=1, ge=1, le=6),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> ZoneSurgeResponse:
    _validate_intelligence_horizon(hours_ahead)
    try:
        zones = generate_surge_analysis(
            db,
            hours_ahead=hours_ahead,
            limit=limit,
        )
    except InsufficientForecastDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return ZoneSurgeResponse(
        forecast_horizon_hours=hours_ahead,
        data_source="synthetic_simulation",
        simulated_only=True,
        zones=[
            {
                "zone_id": zone.zone_id,
                "center_lat": zone.center_lat,
                "center_lng": zone.center_lng,
                "predicted_demand": zone.predicted_demand,
                "actual_recent_demand": zone.actual_recent_demand,
                "available_driver_count": zone.available_driver_count,
                "projected_demand_supply_ratio": (
                    zone.projected_demand_supply_ratio
                ),
                "surge_multiplier": zone.surge_multiplier,
                "simulated_fare_adjustment_percentage": (
                    zone.simulated_fare_adjustment_percentage
                ),
                "explanation": zone.explanation,
            }
            for zone in zones
        ],
    )


@router.get(
    "/repositioning/recommendations",
    response_model=RepositioningRecommendationsResponse,
)
def get_repositioning_recommendations(
    hours_ahead: int = Query(default=1, ge=1, le=6),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> RepositioningRecommendationsResponse:
    _validate_intelligence_horizon(hours_ahead)
    try:
        recommendations = generate_repositioning_recommendations(
            db,
            hours_ahead=hours_ahead,
            limit=limit,
        )
    except InsufficientForecastDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return RepositioningRecommendationsResponse(
        forecast_horizon_hours=hours_ahead,
        data_source="synthetic_simulation",
        recommendations_only=True,
        recommendations=[
            {
                "driver_id": str(item.driver_id),
                "current_zone_id": item.current_zone_id,
                "recommended_zone_id": item.recommended_zone_id,
                "target_center_lat": item.target_center_lat,
                "target_center_lng": item.target_center_lng,
                "approximate_distance_meters": (
                    item.approximate_distance_meters
                ),
                "distance_type": "approximate_straight_line",
                "forecasted_target_demand": item.forecasted_target_demand,
                "target_driver_supply": item.target_driver_supply,
                "reason": item.reason,
            }
            for item in recommendations
        ],
    )
