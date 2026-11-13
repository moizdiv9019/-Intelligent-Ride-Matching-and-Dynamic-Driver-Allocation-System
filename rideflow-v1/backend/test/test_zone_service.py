from datetime import datetime, timedelta, timezone

import pytest

from app.services.zone_service import (
    aggregate_available_driver_supply,
    aggregate_ride_demand,
    classify_zone,
    generate_zone_summary,
    get_zone_for_coordinates,
)


ANALYSIS_TIME = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)


def test_coordinates_in_same_cell_have_consistent_zone_ids():
    first = get_zone_for_coordinates(12.301, 76.641)
    second = get_zone_for_coordinates(12.309, 76.649)

    assert first.zone_id == second.zone_id == "zone:1230:7664"
    assert first.center_lat == second.center_lat == 12.305
    assert first.center_lng == second.center_lng == 76.645
    assert first.min_lat == 12.3
    assert first.max_lat == 12.31
    assert first.min_lng == 76.64
    assert first.max_lng == 76.65


def test_grid_boundaries_enter_the_cell_on_the_north_and_east():
    cell = get_zone_for_coordinates(12.30, 76.65)

    assert (cell.lat_index, cell.lng_index) == (1230, 7665)
    assert (cell.min_lat, cell.min_lng) == (12.3, 76.65)


def test_negative_grid_boundary_uses_floor_index():
    cell = get_zone_for_coordinates(-0.3, -0.1)

    assert (cell.lat_index, cell.lng_index) == (-30, -10)


class MockResult:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class MockSession:
    def __init__(self, demand_rows=(), supply_rows=()):
        self.demand_rows = demand_rows
        self.supply_rows = supply_rows
        self.calls = []

    def execute(self, statement, params):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        if "FROM public.rides AS r" in sql:
            return MockResult(self.demand_rows)
        if "FROM public.driver_profiles AS dp" in sql:
            return MockResult(self.supply_rows)
        raise AssertionError(f"Unexpected query: {sql}")


@pytest.mark.parametrize("window_minutes", [15, 60, 1440])
def test_demand_aggregation_counts_distinct_rides_in_the_requested_window(
    window_minutes,
):
    db = MockSession(
        demand_rows=[
            {"lat_index": 1230, "lng_index": 7664, "demand_count": 4},
        ]
    )

    demand = aggregate_ride_demand(
        db,
        window_minutes=window_minutes,
        analysis_timestamp=ANALYSIS_TIME,
    )

    assert demand == {(1230, 7664): 4}
    sql, params = db.calls[0]
    assert "COUNT(DISTINCT r.id)" in sql
    assert "r.requested_at >= :window_start" in sql
    assert "r.pickup_location IS NOT NULL" in sql
    assert params["window_start"] == ANALYSIS_TIME - timedelta(minutes=window_minutes)


def test_driver_supply_aggregation_filters_eligibility_and_stale_locations():
    db = MockSession(
        supply_rows=[
            {"lat_index": 1230, "lng_index": 7664, "driver_count": 2},
        ]
    )

    supply = aggregate_available_driver_supply(
        db,
        analysis_timestamp=ANALYSIS_TIME,
    )

    assert supply == {(1230, 7664): 2}
    sql, params = db.calls[0]
    assert "dp.verification_status = 'verified'" in sql
    assert "dp.availability_status = 'available'" in sql
    assert "dl.device_recorded_at >= :fresh_since" in sql
    assert "dl.received_at >= :fresh_since" in sql
    assert "v.is_active = TRUE" in sql
    assert "COUNT(DISTINCT dp.user_id)" in sql
    assert params["fresh_since"] == ANALYSIS_TIME - timedelta(seconds=30)
    select_clause = sql.split("FROM public.driver_profiles AS dp", maxsplit=1)[0]
    assert "AS driver_lat" not in select_clause
    assert "AS driver_lng" not in select_clause


def test_zone_shortage_classification_and_zero_driver_supply():
    ratio, status = classify_zone(3, 1)
    no_supply_ratio, no_supply_status = classify_zone(1, 0)

    assert ratio == 3
    assert status == "shortage"
    assert no_supply_ratio is None
    assert no_supply_status == "shortage"


def test_zone_surplus_classification():
    ratio, status = classify_zone(1, 2)

    assert ratio == 0.5
    assert status == "surplus"


def test_zone_balanced_classification():
    ratio, status = classify_zone(1, 1)

    assert ratio == 1
    assert status == "balanced"


def test_empty_summary_omits_zones_without_demand_or_supply():
    db = MockSession()

    summaries = generate_zone_summary(
        db,
        analysis_timestamp=ANALYSIS_TIME,
    )

    assert summaries == []


def test_zero_demand_and_supply_classifies_as_inactive():
    ratio, status = classify_zone(0, 0)

    assert ratio is None
    assert status == "inactive"


def test_summary_combines_demand_and_supply_into_zone_response_data():
    db = MockSession(
        demand_rows=[
            {"lat_index": 1230, "lng_index": 7664, "demand_count": 3},
            {"lat_index": 1231, "lng_index": 7664, "demand_count": 1},
        ],
        supply_rows=[
            {"lat_index": 1230, "lng_index": 7664, "driver_count": 1},
        ],
    )

    summaries = generate_zone_summary(
        db,
        analysis_timestamp=ANALYSIS_TIME,
    )

    assert summaries[0].zone_id == "zone:1230:7664"
    assert summaries[0].recent_demand_count == 3
    assert summaries[0].available_driver_count == 1
    assert summaries[0].demand_supply_ratio == 3
    assert summaries[0].zone_status == "shortage"
    assert summaries[0].analysis_timestamp == ANALYSIS_TIME
    assert summaries[1].zone_status == "shortage"
