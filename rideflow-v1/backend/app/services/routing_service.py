from collections.abc import Sequence

import httpx
from fastapi import HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.schemas.location import Coordinate
from app.schemas.ride import GeoJSONLineString, RoutingResult


class RoutingLeg(BaseModel):
    distance_m: float = Field(ge=0, allow_inf_nan=False)
    duration_s: float = Field(ge=0, allow_inf_nan=False)


class MultiStopRoutingResult(BaseModel):
    distance_m: float = Field(ge=0, allow_inf_nan=False)
    duration_s: float = Field(ge=0, allow_inf_nan=False)
    geometry: GeoJSONLineString
    legs: tuple[RoutingLeg, ...]


def _routing_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Routing service is unavailable.",
    )


async def _request_osrm_routes(
    coordinates: str,
    *,
    alternatives: bool,
) -> list[dict]:
    base_url = settings.routing_base_url
    if not base_url or not base_url.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Routing service is not configured.",
        )

    url = f"{base_url.rstrip('/')}/route/v1/driving/{coordinates}"
    headers = {}
    if settings.routing_api_key:
        headers["Authorization"] = f"Bearer {settings.routing_api_key}"

    params = {"overview": "full", "geometries": "geojson"}
    if alternatives:
        params.update(alternatives="true", steps="false")
    try:
        async with httpx.AsyncClient(
            timeout=settings.routing_timeout_seconds
        ) as client:
            response = await client.get(
                url,
                params=params,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise _routing_unavailable() from exc
    except httpx.RequestError as exc:
        raise _routing_unavailable() from exc
    except httpx.InvalidURL as exc:
        raise _routing_unavailable() from exc

    if not response.is_success:
        raise _routing_unavailable()

    try:
        payload = response.json()
    except ValueError as exc:
        raise _routing_unavailable() from exc

    if not isinstance(payload, dict):
        raise _routing_unavailable()
    if payload.get("code") in ("NoRoute", "NoSegment"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No driving route was found for these coordinates.",
        )
    routes = payload.get("routes")
    if payload.get("code") != "Ok" or not isinstance(routes, list) or not routes:
        raise _routing_unavailable()
    if not all(isinstance(route, dict) for route in routes):
        raise _routing_unavailable()
    return routes


async def get_driving_route(
    pickup: Coordinate,
    destination: Coordinate,
) -> RoutingResult:
    coordinates = f"{pickup.lng},{pickup.lat};{destination.lng},{destination.lat}"
    routes = await _request_osrm_routes(coordinates, alternatives=False)
    route = routes[0]
    geometry = route.get("geometry")
    if not isinstance(geometry, dict):
        raise _routing_unavailable()

    try:
        return RoutingResult(
            distance_m=route.get("distance"),
            duration_s=route.get("duration"),
            geometry=GeoJSONLineString.model_validate(geometry),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise _routing_unavailable() from exc


async def get_multi_stop_driving_routes(
    stops: Sequence[Coordinate],
) -> list[MultiStopRoutingResult]:
    """Fetch full-geometry OSRM alternatives for an ordered sequence of stops."""
    if len(stops) < 2:
        raise ValueError("A multi-stop route requires at least two coordinates.")
    coordinates = ";".join(
        f"{stop.lng},{stop.lat}" for stop in stops
    )
    routes = await _request_osrm_routes(coordinates, alternatives=True)
    results = []
    for route in routes:
        geometry = route.get("geometry")
        raw_legs = route.get("legs")
        if not isinstance(geometry, dict) or not isinstance(raw_legs, list):
            raise _routing_unavailable()
        if len(raw_legs) != len(stops) - 1:
            raise _routing_unavailable()
        try:
            legs = tuple(
                RoutingLeg(
                    distance_m=leg["distance"],
                    duration_s=leg["duration"],
                )
                for leg in raw_legs
                if isinstance(leg, dict)
            )
            if len(legs) != len(raw_legs):
                raise _routing_unavailable()
            results.append(
                MultiStopRoutingResult(
                    distance_m=route.get("distance"),
                    duration_s=route.get("duration"),
                    geometry=GeoJSONLineString.model_validate(geometry),
                    legs=legs,
                )
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise _routing_unavailable() from exc
    if not results:
        raise _routing_unavailable()
    return results
