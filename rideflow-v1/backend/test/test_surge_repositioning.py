from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.services.demand_forecast_service import DemandForecast, ZoneForecast
from app.services.repositioning_service import (
    approximate_straight_line_distance_meters,
    generate_repositioning_recommendations,
)
from app.services.surge_pricing_service import (
    calculate_simulated_surge,
    generate_surge_analysis,
)
from app.services.synthetic_demand_service import (
    generate_synthetic_demand,
    get_synthetic_zones,
)
from app.services.zone_service import get_zone_for_coordinates


ANALYSIS_TIME = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)


class MockResult:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class MockSession:
    def __init__(self, *, demand_rows=(), supply_rows=(), candidate_rows=()):
        self.demand_rows = demand_rows
        self.supply_rows = supply_rows
        self.candidate_rows = candidate_rows
        self.statements = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if "FROM public.driver_profiles AS dp" in sql:
            if "AS driver_lat" in sql:
                return MockResult(self.candidate_rows)
            return MockResult(self.supply_rows)
        if "FROM public.rides AS r" in sql:
            return MockResult(self.demand_rows)
        raise AssertionError(f"Unexpected SQL statement: {sql}")


def _forecast(demands):
    zones = get_synthetic_zones()
    return DemandForecast(
        forecast_horizon_hours=1,
        zones=tuple(
            ZoneForecast(
                zone_id=zone.zone_id,
                center_lat=zone.center_lat,
                center_lng=zone.center_lng,
                predicted_hourly_demand=demands.get(zone.zone_id, 0),
                forecast_timestamp=ANALYSIS_TIME,
            )
            for zone in zones
        ),
        model_name="GradientBoostingRegressor",
        evaluation_mae=1.0,
        evaluation_rmse=1.5,
        data_source="synthetic_simulation",
    )


def test_synthetic_forecast_zones_align_with_live_zone_grid(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.demand_simulation_center_lat",
        12.2965,
    )
    monkeypatch.setattr(
        "app.core.config.settings.demand_simulation_center_lng",
        76.6405,
    )
    zones = get_synthetic_zones()
    live_location_zone = get_zone_for_coordinates(12.2965, 76.6405)

    assert len(zones) == 9
    assert live_location_zone.zone_id == "zone:1229:7664"
    assert live_location_zone.zone_id in {zone.zone_id for zone in zones}
    for zone in zones:
        assert (
            get_zone_for_coordinates(zone.center_lat, zone.center_lng).zone_id
            == zone.zone_id
        )

    records = generate_synthetic_demand(
        history_days=1,
        seed=42,
        end_time=ANALYSIS_TIME,
    )
    assert {record["zone_id"] for record in records} == {
        zone.zone_id for zone in zones
    }


def test_surge_baseline_and_no_predicted_demand_are_one():
    ratio, multiplier, explanation = calculate_simulated_surge(0, 4)

    assert ratio == 0
    assert multiplier == 1
    assert "No predicted demand" in explanation


def test_surge_increases_gradually_after_shortage_threshold():
    below_ratio, below_multiplier, _ = calculate_simulated_surge(1, 1)
    _, moderate_multiplier, _ = calculate_simulated_surge(2, 1)
    _, stronger_multiplier, _ = calculate_simulated_surge(3, 1)

    assert below_ratio < 1.5
    assert below_multiplier == 1
    assert moderate_multiplier == pytest.approx(1.05)
    assert stronger_multiplier > moderate_multiplier


def test_zero_supply_and_large_shortage_are_capped_by_configured_maximum():
    _, zero_supply_multiplier, _ = calculate_simulated_surge(4, 0)
    _, capped_multiplier, _ = calculate_simulated_surge(10_000, 1)

    assert zero_supply_multiplier == 1.5
    assert capped_multiplier == 1.5


def test_surge_analysis_combines_forecast_recent_demand_and_supply(monkeypatch):
    zones = get_synthetic_zones()
    target = zones[4]
    monkeypatch.setattr(
        "app.services.surge_pricing_service.generate_demand_forecast",
        lambda **kwargs: _forecast({target.zone_id: 8}),
    )
    db = MockSession(
        demand_rows=[
            {
                "lat_index": target.cell.lat_index,
                "lng_index": target.cell.lng_index,
                "demand_count": 3,
            }
        ],
        supply_rows=[
            {
                "lat_index": target.cell.lat_index,
                "lng_index": target.cell.lng_index,
                "driver_count": 2,
            }
        ],
    )

    results = generate_surge_analysis(
        db,
        hours_ahead=1,
        analysis_timestamp=ANALYSIS_TIME,
    )
    target_result = next(zone for zone in results if zone.zone_id == target.zone_id)

    assert target_result.predicted_demand == 8
    assert target_result.actual_recent_demand == 3
    assert target_result.available_driver_count == 2
    assert target_result.projected_demand_supply_ratio == 4
    assert target_result.surge_multiplier == 1.25
    assert target_result.simulated_fare_adjustment_percentage == 25
    assert all("UPDATE " not in sql.upper() for sql in db.statements)


def test_repositioning_recommends_idle_surplus_drivers_without_conflicts(
    monkeypatch,
):
    zones = get_synthetic_zones()
    source = zones[0]
    target = zones[4]
    monkeypatch.setattr(
        "app.services.repositioning_service.generate_demand_forecast",
        lambda **kwargs: _forecast({target.zone_id: 10}),
    )
    driver_ids = [UUID(int=index) for index in (1, 2, 3)]
    db = MockSession(
        candidate_rows=[
            {
                "driver_id": driver_id,
                "driver_lat": source.cell.center_lat,
                "driver_lng": source.cell.center_lng,
            }
            for driver_id in driver_ids
        ]
    )

    recommendations = generate_repositioning_recommendations(
        db,
        hours_ahead=1,
        analysis_timestamp=ANALYSIS_TIME,
    )

    assert len(recommendations) == 3
    assert {item.driver_id for item in recommendations} == set(driver_ids)
    assert all(item.current_zone_id == source.zone_id for item in recommendations)
    assert all(item.recommended_zone_id == target.zone_id for item in recommendations)
    assert all(item.current_zone_id != item.recommended_zone_id for item in recommendations)
    assert all(item.forecasted_target_demand == 10 for item in recommendations)
    assert all(item.target_driver_supply >= 0 for item in recommendations)
    assert all(item.approximate_distance_meters > 0 for item in recommendations)
    assert all("straight-line" in item.reason for item in recommendations)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in db.statements)


def test_repositioning_query_filters_ineligible_drivers_and_active_rides(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.repositioning_service.generate_demand_forecast",
        lambda **kwargs: _forecast({}),
    )
    db = MockSession()

    recommendations = generate_repositioning_recommendations(
        db,
        analysis_timestamp=ANALYSIS_TIME,
    )

    assert recommendations == []
    query = db.statements[0]
    assert "dp.verification_status = 'verified'" in query
    assert "dp.availability_status = 'available'" in query
    assert "dl.device_recorded_at >= :fresh_since" in query
    assert "dl.received_at >= :fresh_since" in query
    assert "v.is_active = TRUE" in query
    assert "NOT EXISTS" in query
    assert "ride.assigned_driver_id = dp.user_id" in query
    assert "'requested', 'accepted', 'arriving', 'ongoing'" in query
    assert "UPDATE " not in query.upper()


def test_repositioning_does_not_send_driver_to_current_zone(monkeypatch):
    zones = get_synthetic_zones()
    driver_zone = zones[0]
    other_target = zones[1]
    monkeypatch.setattr(
        "app.services.repositioning_service.generate_demand_forecast",
        lambda **kwargs: _forecast({driver_zone.zone_id: 20}),
    )
    db = MockSession(
        candidate_rows=[
            {
                "driver_id": UUID(int=1),
                "driver_lat": driver_zone.cell.center_lat,
                "driver_lng": driver_zone.cell.center_lng,
            }
        ]
    )

    recommendations = generate_repositioning_recommendations(
        db,
        analysis_timestamp=ANALYSIS_TIME,
    )

    assert recommendations == []
    assert driver_zone.zone_id != other_target.zone_id


def test_distance_is_approximate_great_circle_distance():
    distance = approximate_straight_line_distance_meters(
        12.2965,
        76.6405,
        12.3065,
        76.6405,
    )

    assert distance == pytest.approx(1112, abs=2)
