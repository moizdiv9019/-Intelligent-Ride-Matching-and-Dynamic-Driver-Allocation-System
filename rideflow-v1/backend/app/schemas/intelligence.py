from datetime import datetime

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
    zone_status: str
    analysis_timestamp: datetime
