import httpx
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.location import Coordinate
from app.schemas.ride import GeoJSONLineString, RoutingResult


def _routing_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Routing service is unavailable.",
    )


async def get_driving_route(
    pickup: Coordinate,
    destination: Coordinate,
) -> RoutingResult:
    base_url = settings.routing_base_url
    if not base_url or not base_url.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Routing service is not configured.",
        )

    coordinates = (
        f"{pickup.lng},{pickup.lat};{destination.lng},{destination.lat}"
    )
    url = f"{base_url.rstrip('/')}/route/v1/driving/{coordinates}"
    headers = {}
    if settings.routing_api_key:
        headers["Authorization"] = f"Bearer {settings.routing_api_key}"

    try:
        async with httpx.AsyncClient(
            timeout=settings.routing_timeout_seconds
        ) as client:
            response = await client.get(
                url,
                params={"overview": "full", "geometries": "geojson"},
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

    route = routes[0]
    if not isinstance(route, dict):
        raise _routing_unavailable()
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
