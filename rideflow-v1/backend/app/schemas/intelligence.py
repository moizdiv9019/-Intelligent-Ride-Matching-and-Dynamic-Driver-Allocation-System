from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ZoneBoundsResponse(BaseModel):
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float


class ZoneSummaryResponse(BaseModel):
    zone_id: str
    center_lat: float
    center_lng: float
    bounds: ZoneBoundsResponse
    recent_demand_count: int
    available_driver_count: int
    demand_supply_ratio: float | None
    zone_status: Literal["shortage", "balanced", "surplus", "inactive"]
    analysis_timestamp: datetime


class ForecastZoneResponse(BaseModel):
    zone_id: str
    center_lat: float
    center_lng: float
    predicted_hourly_demand: int
    forecast_timestamp: datetime


class DemandForecastResponse(BaseModel):
    forecast_horizon_hours: Literal[1, 3, 6]
    zones: list[ForecastZoneResponse]
    model_name: str
    evaluation_mae: float
    evaluation_rmse: float
    data_source: Literal["synthetic_simulation"]


class ForecastPeriodResponse(BaseModel):
    start: datetime
    end: datetime


class ForecastEvaluationPointResponse(BaseModel):
    zone_id: str
    timestamp: datetime
    actual_demand: int
    predicted_demand: int
    absolute_error: int


class DemandForecastEvaluationResponse(BaseModel):
    training_dataset_size: int
    training_period: ForecastPeriodResponse
    validation_period: ForecastPeriodResponse
    model_name: str
    mae: float
    rmse: float
    evaluation_label: str
    data_source: Literal["synthetic_simulation"]
    results: list[ForecastEvaluationPointResponse]


class SurgeZoneResponse(BaseModel):
    zone_id: str
    center_lat: float
    center_lng: float
    predicted_demand: int
    actual_recent_demand: int
    available_driver_count: int
    projected_demand_supply_ratio: float
    surge_multiplier: float
    simulated_fare_adjustment_percentage: float
    explanation: str


class ZoneSurgeResponse(BaseModel):
    forecast_horizon_hours: Literal[1, 3, 6]
    data_source: Literal["synthetic_simulation"]
    simulated_only: Literal[True]
    zones: list[SurgeZoneResponse]


class RepositioningRecommendationResponse(BaseModel):
    driver_id: str
    current_zone_id: str
    recommended_zone_id: str
    target_center_lat: float
    target_center_lng: float
    approximate_distance_meters: int
    distance_type: Literal["approximate_straight_line"]
    forecasted_target_demand: int
    target_driver_supply: int
    reason: str


class RepositioningRecommendationsResponse(BaseModel):
    forecast_horizon_hours: Literal[1, 3, 6]
    data_source: Literal["synthetic_simulation"]
    recommendations_only: Literal[True]
    recommendations: list[RepositioningRecommendationResponse]
