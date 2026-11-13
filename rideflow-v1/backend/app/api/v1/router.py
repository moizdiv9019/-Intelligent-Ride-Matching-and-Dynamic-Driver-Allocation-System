from fastapi import APIRouter

from app.api.v1.endpoints import drivers, offers, profiles, rides


router = APIRouter()
router.include_router(profiles.router)
router.include_router(drivers.router)
router.include_router(rides.router)
router.include_router(offers.router)
