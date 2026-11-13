import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_defaults(monkeypatch):
    for name in (
        "APP_NAME",
        "APP_VERSION",
        "APP_ENV",
        "API_PREFIX",
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "DATABASE_URL",
        "FRONTEND_ORIGINS",
        "ROUTING_BASE_URL",
        "ROUTING_API_KEY",
        "ROUTING_TIMEOUT_SECONDS",
        "OFFER_TIMEOUT_SECONDS",
        "OFFER_EXPIRY_POLL_INTERVAL_SECONDS",
        "DRIVER_LOCATION_MAX_AGE_SECONDS",
        "DRIVER_SEARCH_RADIUS_METERS",
        "ZONE_GRID_RESOLUTION_DEGREES",
        "ZONE_SHORTAGE_RATIO_THRESHOLD",
        "ZONE_SURPLUS_RATIO_THRESHOLD",
        "MATCHING_ETA_MAX_SECONDS",
        "MATCHING_DISTANCE_MAX_METERS",
        "MATCHING_IDLE_MAX_SECONDS",
        "MATCHING_ETA_WEIGHT",
        "MATCHING_DISTANCE_WEIGHT",
        "MATCHING_IDLE_TIME_WEIGHT",
        "FARE_BASE_INR",
        "FARE_PER_KM_INR",
        "FARE_PER_MINUTE_INR",
        "FARE_MINIMUM_INR",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "RideFlow API"
    assert settings.app_version == "1.0.0"
    assert settings.app_env == "development"
    assert settings.api_prefix == "/api/v1"
    assert settings.supabase_url is None
    assert settings.supabase_publishable_key is None
    assert settings.database_url is None
    assert settings.routing_base_url is None
    assert settings.routing_api_key is None
    assert settings.routing_timeout_seconds == 10
    assert settings.frontend_origins == ["http://localhost:5173"]
    assert settings.offer_timeout_seconds == 20
    assert settings.offer_expiry_poll_interval_seconds == 5
    assert settings.driver_location_max_age_seconds == 30
    assert settings.driver_search_radius_meters == 5000
    assert settings.zone_grid_resolution_degrees == 0.01
    assert settings.zone_shortage_ratio_threshold == 1.5
    assert settings.zone_surplus_ratio_threshold == 0.75
    assert settings.matching_eta_max_seconds == 1200
    assert settings.matching_distance_max_meters == 5000
    assert settings.matching_idle_max_seconds == 1800
    assert settings.matching_eta_weight == 0.60
    assert settings.matching_distance_weight == 0.25
    assert settings.matching_idle_time_weight == 0.15
    assert settings.fare_base_inr == 30
    assert settings.fare_per_km_inr == 12
    assert settings.fare_per_minute_inr == 1
    assert settings.fare_minimum_inr == 40


def test_matching_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            matching_eta_weight=0.5,
            matching_distance_weight=0.25,
            matching_idle_time_weight=0.15,
        )


def test_zone_ratio_thresholds_must_be_ordered():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            zone_shortage_ratio_threshold=0.5,
            zone_surplus_ratio_threshold=0.75,
        )
