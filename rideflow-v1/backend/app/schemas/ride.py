from datetime import datetime
import math
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.location import Coordinate


RideStatus = Literal[
    "requested",
    "accepted",
    "arriving",
    "ongoing",
    "completed",
    "cancelled",
    "no_driver_found",
]


class RideEstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pickup: Coordinate
    destination: Coordinate


class GeoJSONLineString(BaseModel):
    type: Literal["LineString"]
    coordinates: list[tuple[float, float]] = Field(min_length=2)

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(
        cls, coordinates: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        for longitude, latitude in coordinates:
            if (
                not math.isfinite(longitude)
                or not math.isfinite(latitude)
                or not -180 <= longitude <= 180
                or not -90 <= latitude <= 90
            ):
                raise ValueError("Route geometry contains invalid coordinates.")
        return coordinates


class RoutingResult(BaseModel):
    distance_m: float = Field(ge=0, allow_inf_nan=False)
    duration_s: float = Field(ge=0, allow_inf_nan=False)
    geometry: GeoJSONLineString


class FareBreakdown(BaseModel):
    base_fare: float = Field(ge=0, allow_inf_nan=False)
    distance_fare: float = Field(ge=0, allow_inf_nan=False)
    time_fare: float = Field(ge=0, allow_inf_nan=False)
    minimum_fare_adjustment: float = Field(ge=0, allow_inf_nan=False)
    estimated_total: float = Field(ge=0, allow_inf_nan=False)
    currency: Literal["INR"] = "INR"


class RideEstimateResponse(BaseModel):
    pickup: Coordinate
    destination: Coordinate
    estimated_distance_m: float = Field(ge=0, allow_inf_nan=False)
    estimated_duration_s: float = Field(ge=0, allow_inf_nan=False)
    estimated_fare: float = Field(ge=0, allow_inf_nan=False)
    currency: Literal["INR"] = "INR"
    fare_breakdown: FareBreakdown
    route_geometry: GeoJSONLineString


class RideCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    pickup: Coordinate
    destination: Coordinate
    pickup_address: str = Field(min_length=1, max_length=500)
    destination_address: str = Field(min_length=1, max_length=500)
    pooling_opt_in: bool = False


class PoolingStop(BaseModel):
    passenger_label: Literal["A", "B"]
    stop_type: Literal["pickup", "dropoff"]
    coordinate: Coordinate


class PassengerPoolEstimate(BaseModel):
    passenger_label: Literal["A", "B"]
    individual_distance_m: float = Field(ge=0, allow_inf_nan=False)
    individual_duration_s: float = Field(ge=0, allow_inf_nan=False)
    individual_fare_estimate: float = Field(ge=0, allow_inf_nan=False)
    pooled_distance_m: float = Field(ge=0, allow_inf_nan=False)
    pooled_duration_s: float = Field(ge=0, allow_inf_nan=False)
    detour_distance_m: float = Field(ge=0, allow_inf_nan=False)
    detour_percent: float = Field(ge=0, allow_inf_nan=False)
    additional_duration_s: float = Field(ge=0, allow_inf_nan=False)
    pooled_fare_estimate: float = Field(ge=0, allow_inf_nan=False)


class PoolingFareComparison(BaseModel):
    estimates_only: Literal[True] = True
    currency: Literal["INR"] = "INR"
    discount_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    total_separate_fare_estimate: float = Field(ge=0, allow_inf_nan=False)
    pooled_base_fare_estimate: float = Field(ge=0, allow_inf_nan=False)
    pooled_total_fare_estimate: float = Field(ge=0, allow_inf_nan=False)
    estimated_total_passenger_savings: float
    passenger_fares: list[PassengerPoolEstimate] = Field(min_length=2, max_length=2)


class PoolingPreviewResponse(BaseModel):
    pool_available: bool
    explanation: str
    optimized_stops: list[PoolingStop] = Field(default_factory=list)
    route_geometry: GeoJSONLineString | None = None
    pooled_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pooled_duration_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    fare_comparison: PoolingFareComparison | None = None


class RideVehicleSummary(BaseModel):
    id: UUID
    vehicle_type: str


class RideDetailsResponse(BaseModel):
    ride_id: UUID
    status: RideStatus
    pickup: Coordinate
    destination: Coordinate
    pickup_address: str
    destination_address: str
    estimated_distance_m: int | None
    estimated_duration_s: int | None
    estimated_fare: float | None
    final_distance_m: int | None
    final_fare: float | None
    currency: str
    fare_breakdown: dict[str, Any]
    route_geometry: GeoJSONLineString | None
    assigned_driver_id: UUID | None
    assigned_vehicle: RideVehicleSummary | None
    requested_at: datetime | None
    accepted_at: datetime | None
    arrived_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    dispatch_status: str


class RideCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=500)


class RideLifecycleResponse(BaseModel):
    ride_id: UUID
    status: RideStatus
    assigned_driver_id: UUID | None = None
    assigned_vehicle_id: UUID | None = None
    arrived_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    final_distance_m: int | None = None
    final_fare: float | None = None
    fare_breakdown: dict[str, Any] | None = None
    dispatch_status: str | None = None


class RideHistoryItem(BaseModel):
    ride_id: UUID
    status: RideStatus
    pickup_address: str
    destination_address: str
    estimated_fare: float | None
    final_fare: float | None
    created_at: datetime
    requested_at: datetime | None
    accepted_at: datetime | None
    arrived_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None


class RideHistoryResponse(BaseModel):
    items: list[RideHistoryItem]
    next_cursor: str | None


class RideEventResponse(BaseModel):
    event_id: UUID
    event_type: str
    actor_type: str
    created_at: datetime
    from_status: str | None
    to_status: str | None
    metadata: dict[str, Any]


class DriverOfferResponse(BaseModel):
    offer_id: UUID
    ride_id: UUID
    pickup_address: str
    destination_address: str
    pickup: Coordinate
    pickup_distance_m: int | None
    pickup_eta_s: int | None
    estimated_fare: float | None
    expires_at: datetime


class OfferActionResponse(BaseModel):
    offer_id: UUID
    ride_id: UUID
    status: Literal["accepted", "rejected"]
    dispatch_status: str | None = None


class OfferRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)
