from fastapi import APIRouter

from app.api.v1.endpoints import (
    drivers,
    offers,
    profiles,
    ride_lifecycle,
    rides,
    zone_intelligence,
)


router = APIRouter()
router.include_router(profiles.router)
router.include_router(drivers.router)
router.include_router(rides.router)
router.include_router(offers.router)
router.include_router(ride_lifecycle.router)
router.include_router(zone_intelligence.router)
