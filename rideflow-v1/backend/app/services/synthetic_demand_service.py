"""Reproducible, isolated synthetic demand data for forecasting development."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, pi, sqrt
from typing import TypedDict

from app.core.config import settings
from app.services.zone_service import (
    ZoneCell,
    get_zone_for_coordinates,
    get_zone_for_grid_indices,
)


DEFAULT_END_TIME = datetime(2026, 1, 5, tzinfo=timezone.utc)


class SyntheticDemandRecord(TypedDict):
    zone_id: str
    timestamp: datetime
    hour: int
    day_of_week: int
    is_weekend: bool
    demand_count: int
    center_lat: float
    center_lng: float
    lat_index: int
    lng_index: int


@dataclass(frozen=True)
class SyntheticZone:
    cell: ZoneCell
    zone_index: int

    @property
    def zone_id(self) -> str:
        return self.cell.zone_id

    @property
    def center_lat(self) -> float:
        return self.cell.center_lat

    @property
    def center_lng(self) -> float:
        return self.cell.center_lng


def get_synthetic_zones(
    resolution: float | None = None,
    *,
    center_lat: float | None = None,
    center_lng: float | None = None,
) -> tuple[SyntheticZone, ...]:
    grid_resolution = (
        settings.zone_grid_resolution_degrees if resolution is None else resolution
    )
    origin = get_zone_for_coordinates(
        settings.demand_simulation_center_lat if center_lat is None else center_lat,
        settings.demand_simulation_center_lng if center_lng is None else center_lng,
        grid_resolution,
    )
    zones = []
    zone_index = 0
    for lat_offset in (-1, 0, 1):
        for lng_offset in (-1, 0, 1):
            cell = get_zone_for_grid_indices(
                origin.lat_index + lat_offset,
                origin.lng_index + lng_offset,
                resolution=grid_resolution,
            )
            zones.append(SyntheticZone(cell=cell, zone_index=zone_index))
            zone_index += 1
    return tuple(zones)


def _normalize_hour(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )


def _hourly_demand(
    timestamp: datetime,
    zone_index: int,
    seed: int,
) -> int:
    hour = timestamp.hour
    weekend = timestamp.weekday() >= 5
    morning_peak = exp(-0.5 * ((hour - 8.0) / 1.5) ** 2)
    lunch_peak = exp(-0.5 * ((hour - 13.0) / 2.0) ** 2)
    evening_peak = exp(-0.5 * ((hour - 18.5) / 1.8) ** 2)

    zone_scale = 1.0 + 0.12 * (zone_index % 4)
    commute_sensitivity = 0.8 + 0.12 * (zone_index % 5)
    lunch_sensitivity = 0.7 + 0.15 * (zone_index % 3)
    if weekend:
        pattern = (
            0.45
            + 0.45 * morning_peak
            + 1.25 * lunch_peak
            + 1.20 * evening_peak
        )
    else:
        pattern = (
            0.40
            + 1.75 * commute_sensitivity * morning_peak
            + 0.85 * lunch_sensitivity * lunch_peak
            + 1.90 * commute_sensitivity * evening_peak
        )

    expected_count = (3.5 + 0.65 * zone_index) * zone_scale * pattern
    stable_hour = int(timestamp.timestamp() // 3600)
    noise = random.Random(
        seed + stable_hour * 1_000_003 + zone_index * 97_409
    ).gauss(0, sqrt(expected_count) * 0.55 + 0.5)
    return max(0, int(expected_count + noise + 0.5))


def generate_synthetic_demand(
    *,
    history_days: int | None = None,
    seed: int | None = None,
    end_time: datetime | None = None,
    resolution: float | None = None,
    center_lat: float | None = None,
    center_lng: float | None = None,
) -> list[SyntheticDemandRecord]:
    """Generate an hourly synthetic history without accessing application data."""
    days = settings.demand_forecast_history_days if history_days is None else history_days
    random_seed = settings.demand_forecast_random_seed if seed is None else seed
    if days <= 0:
        raise ValueError("Synthetic history must contain at least one day.")
    zones = get_synthetic_zones(
        resolution,
        center_lat=center_lat,
        center_lng=center_lng,
    )
    if not zones:
        return []

    end_hour = _normalize_hour(end_time or DEFAULT_END_TIME)
    total_hours = days * 24
    first_hour = end_hour - timedelta(hours=total_hours - 1)
    records: list[SyntheticDemandRecord] = []
    for hour_offset in range(total_hours):
        timestamp = first_hour + timedelta(hours=hour_offset)
        for zone in zones:
            records.append(
                {
                    "zone_id": zone.zone_id,
                    "timestamp": timestamp,
                    "hour": timestamp.hour,
                    "day_of_week": timestamp.weekday(),
                    "is_weekend": timestamp.weekday() >= 5,
                    "demand_count": _hourly_demand(
                        timestamp,
                        zone.zone_index,
                        random_seed,
                    ),
                    "center_lat": zone.center_lat,
                    "center_lng": zone.center_lng,
                    "lat_index": zone.cell.lat_index,
                    "lng_index": zone.cell.lng_index,
                }
            )
    return records
