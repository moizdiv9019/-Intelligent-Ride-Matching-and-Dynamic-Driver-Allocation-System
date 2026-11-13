"""Chronological synthetic-demand model training and zone forecast generation."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from math import cos, floor, pi, sin, sqrt

from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from app.core.config import settings
from app.services.synthetic_demand_service import (
    SyntheticDemandRecord,
    generate_synthetic_demand,
    get_synthetic_zones,
)


MODEL_NAME = "GradientBoostingRegressor"
DATA_SOURCE = "synthetic_simulation"
EVALUATION_LABEL = "synthetic_simulation_performance_not_real_world_accuracy"
_MINIMUM_TRAINING_EXAMPLES = 24
_MINIMUM_VALIDATION_EXAMPLES = 8
_MINIMUM_LAG_HOURS = 24

FEATURE_NAMES = (
    "lat_index",
    "lng_index",
    "hour_sin",
    "hour_cos",
    "day_of_week_sin",
    "day_of_week_cos",
    "is_weekend",
    "previous_hour_demand",
    "previous_24_hour_demand",
    "recent_3_hour_mean",
)


class InsufficientForecastDataError(ValueError):
    """Raised when history cannot provide chronological training and validation."""


@dataclass(frozen=True)
class ForecastPeriod:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ForecastEvaluationPoint:
    zone_id: str
    timestamp: datetime
    actual_demand: int
    predicted_demand: int
    absolute_error: int


@dataclass(frozen=True)
class ForecastEvaluation:
    training_dataset_size: int
    training_period: ForecastPeriod
    validation_period: ForecastPeriod
    model_name: str
    mae: float
    rmse: float
    evaluation_label: str
    data_source: str
    results: tuple[ForecastEvaluationPoint, ...]


@dataclass(frozen=True)
class ZoneForecast:
    zone_id: str
    center_lat: float
    center_lng: float
    predicted_hourly_demand: int
    forecast_timestamp: datetime


@dataclass(frozen=True)
class DemandForecast:
    forecast_horizon_hours: int
    zones: tuple[ZoneForecast, ...]
    model_name: str
    evaluation_mae: float
    evaluation_rmse: float
    data_source: str


@dataclass(frozen=True)
class _TrainingExample:
    zone_id: str
    timestamp: datetime
    features: tuple[float, ...]
    target: int


@dataclass(frozen=True)
class _TrainedModel:
    model: GradientBoostingRegressor
    evaluation: ForecastEvaluation


def _round_prediction(prediction: float) -> int:
    return max(0, int(floor(float(prediction) + 0.5)))


def _build_examples(
    records: list[SyntheticDemandRecord],
) -> list[_TrainingExample]:
    by_zone: dict[str, list[SyntheticDemandRecord]] = {}
    for record in records:
        by_zone.setdefault(record["zone_id"], []).append(record)

    examples = []
    for zone_records in by_zone.values():
        zone_records.sort(key=lambda record: record["timestamp"])
        for index in range(_MINIMUM_LAG_HOURS, len(zone_records)):
            current = zone_records[index]
            timestamp = current["timestamp"]
            hour_angle = 2 * pi * timestamp.hour / 24
            weekday_angle = 2 * pi * timestamp.weekday() / 7
            recent = [
                row["demand_count"]
                for row in zone_records[max(0, index - 3) : index]
            ]
            features = (
                float(current["lat_index"]),
                float(current["lng_index"]),
                sin(hour_angle),
                cos(hour_angle),
                sin(weekday_angle),
                cos(weekday_angle),
                float(current["is_weekend"]),
                float(zone_records[index - 1]["demand_count"]),
                float(zone_records[index - 24]["demand_count"]),
                sum(recent) / len(recent),
            )
            examples.append(
                _TrainingExample(
                    zone_id=current["zone_id"],
                    timestamp=timestamp,
                    features=features,
                    target=current["demand_count"],
                )
            )
    examples.sort(key=lambda example: (example.timestamp, example.zone_id))
    return examples


def _train_and_evaluate(
    records: list[SyntheticDemandRecord],
    *,
    seed: int,
) -> _TrainedModel:
    examples = _build_examples(records)
    timestamps = sorted({example.timestamp for example in examples})
    split_index = int(len(timestamps) * 0.8)
    if split_index == 0 or split_index >= len(timestamps):
        raise InsufficientForecastDataError(
            "Synthetic history must provide separate chronological training and validation periods."
        )

    validation_start = timestamps[split_index]
    training_examples = [
        example for example in examples if example.timestamp < validation_start
    ]
    validation_examples = [
        example for example in examples if example.timestamp >= validation_start
    ]
    if (
        len(training_examples) < _MINIMUM_TRAINING_EXAMPLES
        or len(validation_examples) < _MINIMUM_VALIDATION_EXAMPLES
    ):
        raise InsufficientForecastDataError(
            "Synthetic history is too short for both model training and held-out validation."
        )

    model = GradientBoostingRegressor(
        loss="squared_error",
        learning_rate=0.05,
        n_estimators=100,
        max_depth=3,
        random_state=seed,
    )
    model.fit(
        [example.features for example in training_examples],
        [example.target for example in training_examples],
    )
    predicted = [
        _round_prediction(value)
        for value in model.predict(
            [example.features for example in validation_examples]
        )
    ]
    actual = [example.target for example in validation_examples]
    mae = float(mean_absolute_error(actual, predicted))
    rmse = float(sqrt(mean_squared_error(actual, predicted)))
    evaluation = ForecastEvaluation(
        training_dataset_size=len(training_examples),
        training_period=ForecastPeriod(
            start=training_examples[0].timestamp,
            end=training_examples[-1].timestamp,
        ),
        validation_period=ForecastPeriod(
            start=validation_examples[0].timestamp,
            end=validation_examples[-1].timestamp,
        ),
        model_name=MODEL_NAME,
        mae=mae,
        rmse=rmse,
        evaluation_label=EVALUATION_LABEL,
        data_source=DATA_SOURCE,
        results=tuple(
            ForecastEvaluationPoint(
                zone_id=example.zone_id,
                timestamp=example.timestamp,
                actual_demand=example.target,
                predicted_demand=prediction,
                absolute_error=abs(example.target - prediction),
            )
            for example, prediction in zip(validation_examples, predicted)
        ),
    )
    return _TrainedModel(model=model, evaluation=evaluation)


@lru_cache(maxsize=4)
def _load_trained_model(
    history_days: int,
    seed: int,
    resolution: float,
    center_lat: float,
    center_lng: float,
) -> _TrainedModel:
    records = generate_synthetic_demand(
        history_days=history_days,
        seed=seed,
        resolution=resolution,
        center_lat=center_lat,
        center_lng=center_lng,
    )
    return _train_and_evaluate(records, seed=seed)


def get_forecast_evaluation() -> ForecastEvaluation:
    return _load_trained_model(
        settings.demand_forecast_history_days,
        settings.demand_forecast_random_seed,
        settings.zone_grid_resolution_degrees,
        settings.demand_simulation_center_lat,
        settings.demand_simulation_center_lng,
    ).evaluation


def _forecast_features(
    *,
    lat_index: int,
    lng_index: int,
    timestamp: datetime,
    history: list[int],
) -> list[float]:
    hour_angle = 2 * pi * timestamp.hour / 24
    weekday_angle = 2 * pi * timestamp.weekday() / 7
    recent = history[-3:]
    return [
        float(lat_index),
        float(lng_index),
        sin(hour_angle),
        cos(hour_angle),
        sin(weekday_angle),
        cos(weekday_angle),
        float(timestamp.weekday() >= 5),
        float(history[-1]),
        float(history[-24]),
        sum(recent) / len(recent),
    ]


def generate_demand_forecast(
    *,
    hours_ahead: int = 1,
    limit: int = 100,
    now: datetime | None = None,
) -> DemandForecast:
    if hours_ahead not in (1, 3, 6):
        raise ValueError("Forecast horizon must be 1, 3, or 6 hours.")
    if hours_ahead > settings.demand_forecast_max_hours_ahead:
        raise ValueError("Forecast horizon exceeds the configured maximum.")
    if limit <= 0:
        raise ValueError("Forecast result limit must be positive.")

    trained = _load_trained_model(
        settings.demand_forecast_history_days,
        settings.demand_forecast_random_seed,
        settings.zone_grid_resolution_degrees,
        settings.demand_simulation_center_lat,
        settings.demand_simulation_center_lng,
    )
    forecast_now = now or datetime.now(timezone.utc)
    if forecast_now.tzinfo is None:
        forecast_now = forecast_now.replace(tzinfo=timezone.utc)
    current_hour = forecast_now.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    history_records = generate_synthetic_demand(
        history_days=settings.demand_forecast_history_days,
        seed=settings.demand_forecast_random_seed,
        end_time=current_hour,
        resolution=settings.zone_grid_resolution_degrees,
        center_lat=settings.demand_simulation_center_lat,
        center_lng=settings.demand_simulation_center_lng,
    )
    history_by_zone: dict[str, list[int]] = {}
    for record in history_records:
        history_by_zone.setdefault(record["zone_id"], []).append(
            record["demand_count"]
        )
    zones = get_synthetic_zones(
        settings.zone_grid_resolution_degrees,
        center_lat=settings.demand_simulation_center_lat,
        center_lng=settings.demand_simulation_center_lng,
    )
    predictions: dict[str, int] = {}
    forecast_timestamp = current_hour
    for step in range(1, hours_ahead + 1):
        forecast_timestamp = current_hour + timedelta(hours=step)
        for zone in zones:
            zone_history = history_by_zone[zone.zone_id]
            features = _forecast_features(
                lat_index=zone.cell.lat_index,
                lng_index=zone.cell.lng_index,
                timestamp=forecast_timestamp,
                history=zone_history,
            )
            prediction = _round_prediction(trained.model.predict([features])[0])
            zone_history.append(prediction)
            predictions[zone.zone_id] = prediction

    forecasts = tuple(
        ZoneForecast(
            zone_id=zone.zone_id,
            center_lat=zone.center_lat,
            center_lng=zone.center_lng,
            predicted_hourly_demand=predictions[zone.zone_id],
            forecast_timestamp=forecast_timestamp,
        )
        for zone in zones
        if zone.zone_id in predictions
    )
    return DemandForecast(
        forecast_horizon_hours=hours_ahead,
        zones=forecasts[:limit],
        model_name=MODEL_NAME,
        evaluation_mae=trained.evaluation.mae,
        evaluation_rmse=trained.evaluation.rmse,
        data_source=DATA_SOURCE,
    )
