from math import isclose
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "RideFlow API"
    app_version: str = "1.0.0"
    app_env: str = "development"
    api_prefix: str = "/api/v1"

    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    database_url: str | None = None

    frontend_origins: list[str] = ["http://localhost:5173"]

    routing_base_url: str | None = None
    routing_api_key: str | None = None
    routing_timeout_seconds: float = Field(default=10.0, gt=0)

    offer_timeout_seconds: int = Field(default=20, gt=0)
    offer_expiry_poll_interval_seconds: float = Field(default=5.0, gt=0)
    driver_location_max_age_seconds: int = 30
    driver_search_radius_meters: int = 5000
    zone_grid_resolution_degrees: float = Field(
        default=0.01, gt=0, allow_inf_nan=False
    )
    zone_shortage_ratio_threshold: float = Field(
        default=1.5, gt=0, allow_inf_nan=False
    )
    zone_surplus_ratio_threshold: float = Field(
        default=0.75, ge=0, allow_inf_nan=False
    )
    matching_eta_max_seconds: float = Field(
        default=1200.0, gt=0, allow_inf_nan=False
    )
    matching_distance_max_meters: float = Field(
        default=5000.0, gt=0, allow_inf_nan=False
    )
    matching_idle_max_seconds: float = Field(
        default=1800.0, gt=0, allow_inf_nan=False
    )
    matching_eta_weight: float = Field(
        default=0.60, ge=0, le=1, allow_inf_nan=False
    )
    matching_distance_weight: float = Field(
        default=0.25, ge=0, le=1, allow_inf_nan=False
    )
    matching_idle_time_weight: float = Field(
        default=0.15, ge=0, le=1, allow_inf_nan=False
    )
    fare_base_inr: float = Field(default=30.0, ge=0)
    fare_per_km_inr: float = Field(default=12.0, ge=0)
    fare_per_minute_inr: float = Field(default=1.0, ge=0)
    fare_minimum_inr: float = Field(default=40.0, ge=0)

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def validate_matching_weights(self) -> "Settings":
        total = (
            self.matching_eta_weight
            + self.matching_distance_weight
            + self.matching_idle_time_weight
        )
        if not isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError("Driver matching weights must sum to 1.")
        if self.zone_surplus_ratio_threshold >= self.zone_shortage_ratio_threshold:
            raise ValueError(
                "Zone surplus ratio threshold must be lower than the shortage threshold."
            )
        return self


settings = Settings()
