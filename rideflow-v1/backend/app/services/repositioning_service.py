"""Read-only, deterministic idle-driver repositioning recommendations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.demand_forecast_service import generate_demand_forecast
from app.services.zone_service import ZoneCell, get_zone_for_coordinates


EARTH_MEAN_RADIUS_METERS = 6_371_008.8


@dataclass(frozen=True)
class RepositioningRecommendation:
    driver_id: UUID
    current_zone_id: str
    recommended_zone_id: str
    target_center_lat: float
    target_center_lng: float
    approximate_distance_meters: int
    forecasted_target_demand: int
    target_driver_supply: int
    reason: str


@dataclass(frozen=True)
class _Candidate:
    driver_id: UUID
    lat: float
    lng: float
    zone: ZoneCell


def approximate_straight_line_distance_meters(
    start_lat: float,
    start_lng: float,
    end_lat: float,
    end_lng: float,
) -> int:
    """Return haversine distance; this is approximate straight-line, not road distance."""
    lat1, lat2 = radians(start_lat), radians(end_lat)
    delta_lat = lat2 - lat1
    delta_lng = radians(end_lng - start_lng)
    haversine = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    )
    central_angle = 2 * asin(min(1.0, sqrt(haversine)))
    return int(round(EARTH_MEAN_RADIUS_METERS * central_angle))


def _load_eligible_candidates(
    db: Session,
    *,
    analysis_timestamp: datetime,
) -> list[_Candidate]:
    rows = db.execute(
        text(
            """
            SELECT
                dp.user_id AS driver_id,
                ST_Y(dl.location::geometry) AS driver_lat,
                ST_X(dl.location::geometry) AS driver_lng
            FROM public.driver_profiles AS dp
            JOIN public.driver_locations AS dl
              ON dl.driver_user_id = dp.user_id
            WHERE dp.verification_status = 'verified'
              AND dp.availability_status = 'available'
              AND dl.device_recorded_at >= :fresh_since
              AND dl.device_recorded_at <= :analysis_timestamp
              AND dl.received_at >= :fresh_since
              AND dl.received_at <= :analysis_timestamp
              AND dl.location IS NOT NULL
              AND NOT ST_IsEmpty(dl.location::geometry)
              AND ST_GeometryType(dl.location::geometry) = 'ST_Point'
              AND ST_Y(dl.location::geometry) BETWEEN -90 AND 90
              AND ST_X(dl.location::geometry) BETWEEN -180 AND 180
              AND EXISTS (
                    SELECT 1
                    FROM public.vehicles AS v
                    WHERE v.driver_user_id = dp.user_id
                      AND v.is_active = TRUE
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM public.rides AS ride
                    WHERE ride.assigned_driver_id = dp.user_id
                      AND ride.status IN (
                            'requested', 'accepted', 'arriving', 'ongoing'
                      )
              )
            ORDER BY dp.user_id
            """
        ),
        {
            "fresh_since": analysis_timestamp
            - timedelta(seconds=settings.driver_location_max_age_seconds),
            "analysis_timestamp": analysis_timestamp,
        },
    ).mappings().all()
    candidates = []
    for row in rows:
        lat = float(row["driver_lat"])
        lng = float(row["driver_lng"])
        candidates.append(
            _Candidate(
                driver_id=row["driver_id"],
                lat=lat,
                lng=lng,
                zone=get_zone_for_coordinates(lat, lng),
            )
        )
    return candidates


def generate_repositioning_recommendations(
    db: Session,
    *,
    hours_ahead: int = 1,
    limit: int = 50,
    analysis_timestamp: datetime | None = None,
) -> list[RepositioningRecommendation]:
    if hours_ahead not in (1, 3, 6):
        raise ValueError("Forecast horizon must be 1, 3, or 6 hours.")
    if hours_ahead > settings.demand_forecast_max_hours_ahead:
        raise ValueError("Forecast horizon exceeds the configured maximum.")
    if limit <= 0:
        raise ValueError("Recommendation limit must be positive.")
    analyzed_at = analysis_timestamp or datetime.now(timezone.utc)
    forecast = generate_demand_forecast(
        hours_ahead=hours_ahead,
        limit=500,
        now=analyzed_at,
    )
    candidates = _load_eligible_candidates(
        db,
        analysis_timestamp=analyzed_at,
    )

    forecast_by_id = {
        row.zone_id: (row, get_zone_for_coordinates(row.center_lat, row.center_lng))
        for row in forecast.zones
    }
    candidates_by_zone: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        candidates_by_zone.setdefault(candidate.zone.zone_id, []).append(candidate)
    for zone_candidates in candidates_by_zone.values():
        zone_candidates.sort(key=lambda candidate: str(candidate.driver_id))

    supply_by_zone = {
        zone_id: len(zone_candidates)
        for zone_id, zone_candidates in candidates_by_zone.items()
    }
    predicted_by_zone = {
        zone_id: row.predicted_hourly_demand
        for zone_id, (row, _) in forecast_by_id.items()
    }

    recommendations = []
    assigned_driver_ids: set[UUID] = set()
    while len(recommendations) < limit:
        shortage_zones = []
        for zone_id, demand in predicted_by_zone.items():
            supply = supply_by_zone.get(zone_id, 0)
            ratio = demand / max(supply, 1)
            if demand > 0 and (
                supply == 0 or ratio >= settings.zone_shortage_ratio_threshold
            ):
                shortage_zones.append((zone_id, demand, supply, ratio))
        shortage_zones.sort(
            key=lambda item: (-item[3], -item[1], item[0])
        )
        if not shortage_zones:
            break

        surplus_sources = [
            zone_id
            for zone_id, zone_candidates in candidates_by_zone.items()
            if zone_id in predicted_by_zone
            and supply_by_zone.get(zone_id, 0) > predicted_by_zone[zone_id]
            and any(
                candidate.driver_id not in assigned_driver_ids
                for candidate in zone_candidates
            )
        ]
        surplus_sources.sort(
            key=lambda zone_id: (
                -(supply_by_zone[zone_id] - predicted_by_zone[zone_id]),
                zone_id,
            )
        )
        selected = None
        for source_zone_id in surplus_sources:
            target = next(
                (
                    shortage
                    for shortage in shortage_zones
                    if shortage[0] != source_zone_id
                ),
                None,
            )
            if target is None:
                continue
            candidate = next(
                candidate
                for candidate in candidates_by_zone[source_zone_id]
                if candidate.driver_id not in assigned_driver_ids
            )
            selected = (candidate, source_zone_id, target)
            break
        if selected is None:
            break

        candidate, source_zone_id, target = selected
        target_zone_id, target_demand, target_supply, ratio = target
        _, target_cell = forecast_by_id[target_zone_id]
        severity = "no eligible supply" if target_supply == 0 else (
            f"projected demand-to-supply ratio {ratio:.2f}"
        )
        recommendations.append(
            RepositioningRecommendation(
                driver_id=candidate.driver_id,
                current_zone_id=source_zone_id,
                recommended_zone_id=target_zone_id,
                target_center_lat=target_cell.center_lat,
                target_center_lng=target_cell.center_lng,
                approximate_distance_meters=approximate_straight_line_distance_meters(
                    candidate.lat,
                    candidate.lng,
                    target_cell.center_lat,
                    target_cell.center_lng,
                ),
                forecasted_target_demand=target_demand,
                target_driver_supply=target_supply,
                reason=(
                    f"Move from surplus zone {source_zone_id} to {target_zone_id}: "
                    f"forecast demand {target_demand}, current eligible supply "
                    f"{target_supply} ({severity}) over {hours_ahead} hour(s). "
                    "Distance is approximate straight-line, not road distance."
                ),
            )
        )
        assigned_driver_ids.add(candidate.driver_id)
        supply_by_zone[source_zone_id] -= 1
        supply_by_zone[target_zone_id] = target_supply + 1

    return recommendations
