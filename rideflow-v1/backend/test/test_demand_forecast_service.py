from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import sqrt

import pytest
from sklearn.ensemble import GradientBoostingRegressor

from app.services.demand_forecast_service import (
    InsufficientForecastDataError,
    _load_trained_model,
    _train_and_evaluate,
    generate_demand_forecast,
    get_forecast_evaluation,
)
from app.services.synthetic_demand_service import generate_synthetic_demand


FIXED_END = datetime(2026, 11, 14, tzinfo=timezone.utc)


def test_synthetic_dataset_is_reproducible_for_same_seed_and_period():
    first = generate_synthetic_demand(
        history_days=3,
        seed=177,
        end_time=FIXED_END,
    )
    second = generate_synthetic_demand(
        history_days=3,
        seed=177,
        end_time=FIXED_END,
    )

    assert first == second


def test_synthetic_dataset_has_90_days_and_at_least_eight_zones():
    records = generate_synthetic_demand(
        history_days=90,
        seed=42,
        end_time=FIXED_END,
    )

    assert len(records) == 90 * 24 * 9
    assert len({record["zone_id"] for record in records}) >= 8
    assert all(
        {"zone_id", "timestamp", "hour", "day_of_week", "is_weekend", "demand_count"}
        <= record.keys()
        for record in records[:10]
    )
    assert all(
        isinstance(record["demand_count"], int) and record["demand_count"] >= 0
        for record in records
    )


def test_synthetic_demand_contains_commute_peaks_and_weekend_variation():
    records = generate_synthetic_demand(
        history_days=90,
        seed=42,
        end_time=FIXED_END,
    )
    by_hour: dict[int, list[int]] = defaultdict(list)
    weekdays = []
    weekends = []
    for record in records:
        by_hour[record["hour"]].append(record["demand_count"])
        if record["is_weekend"]:
            weekends.append(record["demand_count"])
        else:
            weekdays.append(record["demand_count"])

    average = lambda counts: sum(counts) / len(counts)
    morning = average([count for hour in (7, 8, 9) for count in by_hour[hour]])
    evening = average([count for hour in (17, 18, 19, 20) for count in by_hour[hour]])
    overnight = average([count for hour in (1, 2, 3, 4) for count in by_hour[hour]])

    assert morning > overnight
    assert evening > overnight
    assert average(weekdays) != average(weekends)


def test_model_training_uses_chronological_holdout_and_computes_metrics():
    records = generate_synthetic_demand(
        history_days=14,
        seed=42,
        end_time=FIXED_END,
    )

    trained = _train_and_evaluate(records, seed=42)
    evaluation = trained.evaluation

    assert isinstance(trained.model, GradientBoostingRegressor)
    assert evaluation.training_dataset_size > 0
    assert evaluation.training_period.end < evaluation.validation_period.start
    assert evaluation.results
    expected_mae = sum(point.absolute_error for point in evaluation.results) / len(
        evaluation.results
    )
    expected_rmse = sqrt(
        sum(point.absolute_error**2 for point in evaluation.results)
        / len(evaluation.results)
    )
    assert evaluation.mae == pytest.approx(expected_mae)
    assert evaluation.rmse == pytest.approx(expected_rmse)
    assert evaluation.evaluation_label.startswith("synthetic_simulation")
    assert evaluation.data_source == "synthetic_simulation"


def test_forecast_model_initialization_is_cached(monkeypatch):
    _load_trained_model.cache_clear()
    original_train = _train_and_evaluate
    training_calls = 0

    def track_training(records, *, seed):
        nonlocal training_calls
        training_calls += 1
        return original_train(records, seed=seed)

    monkeypatch.setattr(
        "app.services.demand_forecast_service._train_and_evaluate",
        track_training,
    )
    first = get_forecast_evaluation()
    second = get_forecast_evaluation()

    assert first is second
    assert training_calls == 1


@pytest.mark.parametrize("hours_ahead", [1, 3, 6])
def test_forecast_horizons_return_non_negative_zone_predictions(hours_ahead):
    forecast = generate_demand_forecast(
        hours_ahead=hours_ahead,
        limit=100,
        now=FIXED_END,
    )

    assert forecast.forecast_horizon_hours == hours_ahead
    assert len(forecast.zones) >= 8
    assert all(zone.predicted_hourly_demand >= 0 for zone in forecast.zones)
    assert all(
        zone.forecast_timestamp
        == FIXED_END + timedelta(hours=hours_ahead)
        for zone in forecast.zones
    )
    assert forecast.model_name == "GradientBoostingRegressor"
    assert forecast.data_source == "synthetic_simulation"


def test_insufficient_training_data_raises_clear_error():
    records = generate_synthetic_demand(
        history_days=1,
        seed=42,
        end_time=FIXED_END,
    )

    with pytest.raises(InsufficientForecastDataError, match="chronological"):
        _train_and_evaluate(records, seed=42)
