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
    assert settings.fare_base_inr == 30
    assert settings.fare_per_km_inr == 12
    assert settings.fare_per_minute_inr == 1
    assert settings.fare_minimum_inr == 40
