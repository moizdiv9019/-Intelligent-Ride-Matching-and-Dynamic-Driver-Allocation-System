from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)


class NearbyDriverResponse(BaseModel):
    driver_id: UUID
    lat: float
    lng: float
    vehicle_type: Literal["bike", "auto", "car", "accessible_van", "other"]
    distance_m: float = Field(ge=0, allow_inf_nan=False)
