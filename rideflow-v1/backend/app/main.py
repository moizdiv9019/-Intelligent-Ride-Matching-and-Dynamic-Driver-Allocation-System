import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as api_router
from app.core.config import settings
from app.workers.offer_expiry_worker import run_offer_expiry_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_offer_expiry_worker(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await worker_task

app = FastAPI(
    title=settings.app_name,
    description="RideFlow ride-hailing backend",
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
    }

@app.get("/")
def root():
    return {"message": "Welcome to RideFlow API"}
