from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


VehicleType = Literal["bike", "auto", "car", "accessible_van", "other"]
DriverVerificationStatus = Literal["pending", "verified", "rejected"]
DriverAvailabilityStatus = Literal["offline", "available", "busy"]


class DriverOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_type: VehicleType
    make_model: str | None = None
    registration_number: str = Field(min_length=1)
    seat_capacity: int = Field(ge=1, le=20)
    wheelchair_accessible: bool = False


class DriverAvailabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability_status: Literal["available", "offline"]


class DriverLocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    accuracy_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    heading_deg: float | None = Field(default=None, ge=0, lt=360, allow_inf_nan=False)
    speed_mps: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    device_recorded_at: datetime

    @field_validator("device_recorded_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("device_recorded_at must include a timezone.")
        return value


class DriverProfileResponse(BaseModel):
    user_id: UUID
    verification_status: DriverVerificationStatus
    availability_status: DriverAvailabilityStatus
    last_became_available_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VehicleResponse(BaseModel):
    id: UUID
    driver_user_id: UUID
    vehicle_type: VehicleType
    make_model: str | None
    registration_number: str | None
    seat_capacity: int
    wheelchair_accessible: bool
    is_active: bool
    created_at: datetime


class DriverOnboardingResponse(BaseModel):
    driver_profile: DriverProfileResponse
    vehicle: VehicleResponse


class DriverMeResponse(BaseModel):
    driver_profile: DriverProfileResponse
    active_vehicle: VehicleResponse | None


class DriverAvailabilityResponse(BaseModel):
    user_id: UUID
    availability_status: Literal["available", "offline"]
    last_became_available_at: datetime | None


class DriverLocationResponse(BaseModel):
    driver_user_id: UUID
    received_at: datetime
