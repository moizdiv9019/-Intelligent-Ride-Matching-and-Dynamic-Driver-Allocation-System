"""Deterministic simulated surge intelligence; does not alter ride fares."""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.demand_forecast_service import (
    ZoneForecast,
    generate_demand_forecast,
)
from app.services.zone_service import (
    ZoneCell,
    aggregate_available_driver_supply,
    aggregate_ride_demand,
    get_zone_for_coordinates,
    get_zone_for_grid_indices,
)


@dataclass(frozen=True)
class SurgeZone:
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


def calculate_simulated_surge(
    predicted_demand: int,
    available_driver_count: int,
    *,
    ratio_threshold: float | None = None,
    increment_per_ratio: float | None = None,
    max_multiplier: float | None = None,
) -> tuple[float, float, str]:
    if predicted_demand < 0 or available_driver_count < 0:
        raise ValueError("Demand and driver counts cannot be negative.")
    threshold = (
        settings.surge_ratio_threshold
        if ratio_threshold is None
        else ratio_threshold
    )
    increment = (
        settings.surge_increment_per_ratio
        if increment_per_ratio is None
        else increment_per_ratio
    )
    maximum = (
        settings.surge_max_multiplier
        if max_multiplier is None
        else max_multiplier
    )
    if not isfinite(threshold) or threshold <= 0:
        raise ValueError("Surge ratio threshold must be positive and finite.")
    if not isfinite(increment) or increment <= 0:
        raise ValueError("Surge increment must be positive and finite.")
    if not isfinite(maximum) or maximum < 1:
        raise ValueError("Maximum surge multiplier must be finite and at least 1.")

    projected_ratio = predicted_demand / max(available_driver_count, 1)
    if predicted_demand == 0:
        return projected_ratio, 1.0, "No predicted demand; simulated surge is disabled."
    if available_driver_count == 0:
        return (
            projected_ratio,
            maximum,
            "Predicted demand with no eligible available drivers; maximum simulated multiplier applied.",
        )

    excess_ratio = max(0.0, projected_ratio - threshold)
    multiplier = min(maximum, 1.0 + excess_ratio * increment)
    if multiplier == 1.0:
        explanation = (
            f"Projected demand-to-supply ratio {projected_ratio:.2f} "
            f"does not exceed threshold {threshold:.2f}; baseline multiplier."
        )
    else:
        explanation = (
            f"Projected ratio {projected_ratio:.2f} exceeds threshold "
            f"{threshold:.2f}; gradual simulated adjustment is capped at "
            f"{maximum:.2f}x."
        )
    return projected_ratio, multiplier, explanation


def _forecast_cell(forecast_zone: ZoneForecast, resolution: float) -> ZoneCell:
    return get_zone_for_coordinates(
        forecast_zone.center_lat,
        forecast_zone.center_lng,
        resolution,
    )


def generate_surge_analysis(
    db: Session,
    *,
    hours_ahead: int = 1,
    limit: int = 100,
    analysis_timestamp: datetime | None = None,
) -> list[SurgeZone]:
    if hours_ahead not in (1, 3, 6):
        raise ValueError("Forecast horizon must be 1, 3, or 6 hours.")
    if hours_ahead > settings.demand_forecast_max_hours_ahead:
        raise ValueError("Forecast horizon exceeds the configured maximum.")
    if limit <= 0:
        raise ValueError("Surge result limit must be positive.")
    analyzed_at = analysis_timestamp or datetime.now(timezone.utc)
    forecast = generate_demand_forecast(
        hours_ahead=hours_ahead,
        limit=max(limit, 100),
        now=analyzed_at,
    )
    resolution = settings.zone_grid_resolution_degrees
    recent_demand = aggregate_ride_demand(
        db,
        window_minutes=60,
        analysis_timestamp=analyzed_at,
        resolution=resolution,
    )
    available_supply = aggregate_available_driver_supply(
        db,
        analysis_timestamp=analyzed_at,
        resolution=resolution,
    )

    cells: dict[tuple[int, int], ZoneCell] = {}
    predicted_by_cell: dict[tuple[int, int], int] = {}
    for zone in forecast.zones:
        cell = _forecast_cell(zone, resolution)
        key = (cell.lat_index, cell.lng_index)
        cells[key] = cell
        predicted_by_cell[key] = zone.predicted_hourly_demand
    for indexes in set(recent_demand) | set(available_supply):
        cells.setdefault(
            indexes,
            get_zone_for_grid_indices(*indexes, resolution=resolution),
        )

    results = []
    for indexes, cell in cells.items():
        predicted = predicted_by_cell.get(indexes, 0)
        driver_count = available_supply.get(indexes, 0)
        ratio, multiplier, explanation = calculate_simulated_surge(
            predicted,
            driver_count,
        )
        results.append(
            SurgeZone(
                zone_id=cell.zone_id,
                center_lat=cell.center_lat,
                center_lng=cell.center_lng,
                predicted_demand=predicted,
                actual_recent_demand=recent_demand.get(indexes, 0),
                available_driver_count=driver_count,
                projected_demand_supply_ratio=ratio,
                surge_multiplier=multiplier,
                simulated_fare_adjustment_percentage=round(
                    (multiplier - 1.0) * 100.0,
                    2,
                ),
                explanation=explanation,
            )
        )
    results.sort(
        key=lambda zone: (
            -zone.surge_multiplier,
            -zone.projected_demand_supply_ratio,
            zone.zone_id,
        )
    )
    return results[:limit]
