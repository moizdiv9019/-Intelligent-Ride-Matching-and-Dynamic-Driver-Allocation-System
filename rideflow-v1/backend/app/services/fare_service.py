import math
from decimal import Decimal, ROUND_HALF_UP

from app.core.config import settings
from app.schemas.ride import FareBreakdown


_CENT = Decimal("0.01")


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def calculate_fare(distance_m: float, duration_s: float) -> FareBreakdown:
    if (
        not math.isfinite(distance_m)
        or not math.isfinite(duration_s)
        or distance_m < 0
        or duration_s < 0
    ):
        raise ValueError("Distance and duration must be finite non-negative values.")

    base_fare = _rounded(Decimal(str(settings.fare_base_inr)))
    distance_fare = _rounded(
        Decimal(str(distance_m))
        / Decimal("1000")
        * Decimal(str(settings.fare_per_km_inr))
    )
    time_fare = _rounded(
        Decimal(str(duration_s))
        / Decimal("60")
        * Decimal(str(settings.fare_per_minute_inr))
    )
    subtotal = base_fare + distance_fare + time_fare
    minimum_fare = _rounded(Decimal(str(settings.fare_minimum_inr)))
    minimum_adjustment = _rounded(max(minimum_fare - subtotal, Decimal("0")))
    total = _rounded(subtotal + minimum_adjustment)

    return FareBreakdown(
        base_fare=float(base_fare),
        distance_fare=float(distance_fare),
        time_fare=float(time_fare),
        minimum_fare_adjustment=float(minimum_adjustment),
        estimated_total=float(total),
        currency="INR",
    )
