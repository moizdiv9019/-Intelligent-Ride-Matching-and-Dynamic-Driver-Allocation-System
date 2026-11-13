"""Demo-scale geographic grid analysis for aggregate ride demand and supply."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from math import isfinite

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings


@dataclass(frozen=True)
class ZoneCell:
    zone_id: str
    lat_index: int
    lng_index: int
    center_lat: float
    center_lng: float
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float


@dataclass(frozen=True)
class ZoneSummary:
    zone_id: str
    center_lat: float
    center_lng: float
    bounds: dict[str, float]
    recent_demand_count: int
    available_driver_count: int
    demand_supply_ratio: float | None
    zone_status: str
    analysis_timestamp: datetime


def _grid_index(coordinate: float, resolution: float) -> int:
    return int(
        (Decimal(str(coordinate)) / Decimal(str(resolution))).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )


def _zone_from_indexes(
    lat_index: int,
    lng_index: int,
    resolution: float,
) -> ZoneCell:
    decimal_resolution = Decimal(str(resolution))
    decimal_min_lat = Decimal(lat_index) * decimal_resolution
    decimal_min_lng = Decimal(lng_index) * decimal_resolution
    decimal_max_lat = decimal_min_lat + decimal_resolution
    decimal_max_lng = decimal_min_lng + decimal_resolution
    return ZoneCell(
        zone_id=f"zone:{lat_index}:{lng_index}",
        lat_index=lat_index,
        lng_index=lng_index,
        center_lat=float(decimal_min_lat + decimal_resolution / 2),
        center_lng=float(decimal_min_lng + decimal_resolution / 2),
        min_lat=float(decimal_min_lat),
        max_lat=float(decimal_max_lat),
        min_lng=float(decimal_min_lng),
        max_lng=float(decimal_max_lng),
    )


def get_zone_for_coordinates(
    lat: float,
    lng: float,
    resolution: float | None = None,
) -> ZoneCell:
    """Map coordinates to a deterministic decimal-degree grid cell.

    This fixed-degree grid is a demo-scale approximation: cell dimensions in
    meters vary by latitude and it is not intended for precision geospatial work.
    """
    grid_resolution = (
        settings.zone_grid_resolution_degrees if resolution is None else resolution
    )
    if not isfinite(lat) or not -90 <= lat <= 90:
        raise ValueError("Latitude must be finite and between -90 and 90.")
    if not isfinite(lng) or not -180 <= lng <= 180:
        raise ValueError("Longitude must be finite and between -180 and 180.")
    if not isfinite(grid_resolution) or grid_resolution <= 0:
        raise ValueError("Grid resolution must be a positive finite number.")

    lat_index = _grid_index(lat, grid_resolution)
    lng_index = _grid_index(lng, grid_resolution)
    return _zone_from_indexes(lat_index, lng_index, grid_resolution)


def aggregate_ride_demand(
    db: Session,
    *,
    window_minutes: int,
    analysis_timestamp: datetime,
    resolution: float | None = None,
) -> dict[tuple[int, int], int]:
    if window_minutes <= 0:
        raise ValueError("Demand window must be a positive number of minutes.")
    grid_resolution = (
        settings.zone_grid_resolution_degrees if resolution is None else resolution
    )
    rows = db.execute(
        text(
            """
            SELECT
                FLOOR(
                    ST_Y(r.pickup_location::geometry)::numeric
                    / CAST(:resolution AS numeric)
                )::integer AS lat_index,
                FLOOR(
                    ST_X(r.pickup_location::geometry)::numeric
                    / CAST(:resolution AS numeric)
                )::integer AS lng_index,
                COUNT(DISTINCT r.id)::integer AS demand_count
            FROM public.rides AS r
            WHERE r.requested_at >= :window_start
              AND r.requested_at <= :analysis_timestamp
              AND r.pickup_location IS NOT NULL
              AND NOT ST_IsEmpty(r.pickup_location::geometry)
              AND ST_GeometryType(r.pickup_location::geometry) = 'ST_Point'
              AND ST_Y(r.pickup_location::geometry) BETWEEN -90 AND 90
              AND ST_X(r.pickup_location::geometry) BETWEEN -180 AND 180
            GROUP BY lat_index, lng_index
            """
        ),
        {
            "resolution": grid_resolution,
            "window_start": analysis_timestamp - timedelta(minutes=window_minutes),
            "analysis_timestamp": analysis_timestamp,
        },
    ).mappings().all()
    return {
        (int(row["lat_index"]), int(row["lng_index"])): int(row["demand_count"])
        for row in rows
    }


def aggregate_available_driver_supply(
    db: Session,
    *,
    analysis_timestamp: datetime,
    resolution: float | None = None,
) -> dict[tuple[int, int], int]:
    grid_resolution = (
        settings.zone_grid_resolution_degrees if resolution is None else resolution
    )
    rows = db.execute(
        text(
            """
            SELECT
                FLOOR(
                    ST_Y(dl.location::geometry)::numeric
                    / CAST(:resolution AS numeric)
                )::integer AS lat_index,
                FLOOR(
                    ST_X(dl.location::geometry)::numeric
                    / CAST(:resolution AS numeric)
                )::integer AS lng_index,
                COUNT(DISTINCT dp.user_id)::integer AS driver_count
            FROM public.driver_profiles AS dp
            JOIN public.driver_locations AS dl
              ON dl.driver_user_id = dp.user_id
            WHERE dp.verification_status = 'verified'
              AND dp.availability_status = 'available'
              AND dl.device_recorded_at >= :fresh_since
              AND dl.device_recorded_at <= :analysis_timestamp
              AND dl.received_at >= :fresh_since
              AND dl.received_at <= :analysis_timestamp
              AND EXISTS (
                    SELECT 1
                    FROM public.vehicles AS v
                    WHERE v.driver_user_id = dp.user_id
                      AND v.is_active = TRUE
              )
              AND dl.location IS NOT NULL
              AND NOT ST_IsEmpty(dl.location::geometry)
              AND ST_GeometryType(dl.location::geometry) = 'ST_Point'
              AND ST_Y(dl.location::geometry) BETWEEN -90 AND 90
              AND ST_X(dl.location::geometry) BETWEEN -180 AND 180
            GROUP BY lat_index, lng_index
            """
        ),
        {
            "resolution": grid_resolution,
            "fresh_since": analysis_timestamp
            - timedelta(seconds=settings.driver_location_max_age_seconds),
            "analysis_timestamp": analysis_timestamp,
        },
    ).mappings().all()
    return {
        (int(row["lat_index"]), int(row["lng_index"])): int(row["driver_count"])
        for row in rows
    }


def classify_zone(
    demand_count: int,
    driver_count: int,
    *,
    shortage_threshold: float | None = None,
    surplus_threshold: float | None = None,
) -> tuple[float | None, str]:
    if demand_count < 0 or driver_count < 0:
        raise ValueError("Demand and driver counts cannot be negative.")
    shortage_limit = (
        settings.zone_shortage_ratio_threshold
        if shortage_threshold is None
        else shortage_threshold
    )
    surplus_limit = (
        settings.zone_surplus_ratio_threshold
        if surplus_threshold is None
        else surplus_threshold
    )
    if surplus_limit < 0 or shortage_limit <= surplus_limit:
        raise ValueError("Zone ratio thresholds are invalid.")
    if demand_count == 0 and driver_count == 0:
        return None, "inactive"
    if driver_count == 0:
        return None, "shortage"

    ratio = demand_count / driver_count
    if ratio >= shortage_limit:
        return ratio, "shortage"
    if ratio <= surplus_limit:
        return ratio, "surplus"
    return ratio, "balanced"


def generate_zone_summary(
    db: Session,
    *,
    window_minutes: int = 60,
    limit: int = 100,
    analysis_timestamp: datetime | None = None,
) -> list[ZoneSummary]:
    if limit <= 0:
        raise ValueError("Zone result limit must be positive.")
    analyzed_at = analysis_timestamp or datetime.now(timezone.utc)
    resolution = settings.zone_grid_resolution_degrees
    demand_by_cell = aggregate_ride_demand(
        db,
        window_minutes=window_minutes,
        analysis_timestamp=analyzed_at,
        resolution=resolution,
    )
    supply_by_cell = aggregate_available_driver_supply(
        db,
        analysis_timestamp=analyzed_at,
        resolution=resolution,
    )
    cell_indexes = set(demand_by_cell) | set(supply_by_cell)
    summaries = []
    for lat_index, lng_index in cell_indexes:
        cell = _zone_from_indexes(lat_index, lng_index, resolution)
        demand_count = demand_by_cell.get((lat_index, lng_index), 0)
        driver_count = supply_by_cell.get((lat_index, lng_index), 0)
        ratio, zone_status = classify_zone(demand_count, driver_count)
        summaries.append(
            ZoneSummary(
                zone_id=cell.zone_id,
                center_lat=cell.center_lat,
                center_lng=cell.center_lng,
                bounds={
                    "min_lat": cell.min_lat,
                    "max_lat": cell.max_lat,
                    "min_lng": cell.min_lng,
                    "max_lng": cell.max_lng,
                },
                recent_demand_count=demand_count,
                available_driver_count=driver_count,
                demand_supply_ratio=ratio,
                zone_status=zone_status,
                analysis_timestamp=analyzed_at,
            )
        )
    summaries.sort(
        key=lambda summary: (
            -summary.recent_demand_count,
            -summary.available_driver_count,
            summary.zone_id,
        )
    )
    return summaries[:limit]
