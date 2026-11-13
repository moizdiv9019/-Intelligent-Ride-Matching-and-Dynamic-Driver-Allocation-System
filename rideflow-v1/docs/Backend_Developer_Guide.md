# RideFlow V1 — Backend Developer Guide

**Document:** Backend implementation manual  
**Version:** 1.0  
**Project:** RideFlow — browser-based ride-hailing application  
**Audience:** Backend developer (Python/FastAPI beginner to intermediate)  
**Backend stack:** FastAPI + Python, Supabase PostgreSQL + PostGIS, Supabase Auth, Supabase Realtime, OSRM-compatible routing provider  
**Related document:** RideFlow V1 API Contract (20 endpoints)

---

## 1. What you are building

Build the server that controls the business logic for RideFlow V1. The React frontend must not decide which driver gets a ride, whether a driver can accept an offer, what a final fare is, or whether a ride-state transition is valid. FastAPI and PostgreSQL own those decisions.

The backend must provide:

1. Authentication verification for Supabase access tokens.
2. Profile and role management.
3. Driver onboarding and vehicle records.
4. Secure driver availability and latest GPS location updates.
5. Nearby-driver filtering with PostGIS.
6. Road-distance and ETA estimation through an OSRM-compatible routing provider.
7. Estimated and final fare calculation.
8. Sequential one-driver-at-a-time offer dispatch.
9. Offer acceptance/rejection/expiry handling without double assignment.
10. Ride lifecycle, cancellation, event history and ride history.
11. Supabase Realtime-compatible database changes with row-level security.
12. Production deployment of the FastAPI application.

### V1 boundaries

Keep the backend as one **modular monolith**. Do not introduce microservices, Kafka, Redis, Kubernetes, or Celery for the hackathon version. Use PostgreSQL transactions and constraints for correctness.

The following are not currently included in the agreed V1 API contract:

- Payment collection or refunds.
- Address autocomplete/geocoding.
- An admin API/UI for approving drivers.
- SMS/push notifications.

For the demo, driver verification can be performed manually by a trusted project administrator in the database/dashboard. Do not let a driver approve themselves. Add payment, geocoding, and admin endpoints only after the team agrees on their scope.

---

## 2. Agreed architecture

```text
React + Vite browser app
       |
       | Supabase Auth (sign in / sign up / Google OAuth)
       v
Supabase Auth  ---- issues access-token JWT
       |
       | Authorization: Bearer <access-token>
       v
FastAPI  /api/v1
       |
       +---- verifies token and obtains authenticated user ID
       |
       +---- validates role and ride permissions
       |
       +---- SQLAlchemy / psycopg
       |         |
       |         v
       |    Supabase PostgreSQL + PostGIS
       |         |
       |         +---- rides, drivers, vehicles, offers, locations, events
       |
       +---- OSRM-compatible routing service (road distance / ETA / route)
       |
       +---- sequential dispatch and offer-expiry worker

Supabase Realtime -> notifies authorised browser clients of table changes
Browser receives event -> calls FastAPI REST endpoint to refresh authoritative data
```

### Core rules

- Supabase Auth owns signup, login, password handling and Google OAuth.
- FastAPI receives the bearer token and validates it. It never trusts a `user_id` sent in a request body as proof of identity.
- Read the application role (`passenger`, `driver`, `admin`) from `user_roles`. The PostgreSQL JWT role claim `authenticated` is not the RideFlow application role.
- FastAPI is the authority for all ride business operations.
- Realtime is only for notifying the UI. The frontend re-fetches the ride/offer/location through FastAPI after an event.
- Never send Supabase service keys or database credentials to the browser.
- Use a database transaction and database constraints for all operations where two users/workers might race.

---

## 3. Step 0 — Install development tools

Install:

1. Python 3.12 (or a currently supported Python version compatible with the chosen FastAPI/Pydantic packages).
2. VS Code or another Python editor.
3. Git.
4. PostgreSQL client tools (`psql`) if available; Supabase SQL Editor can be used for initial setup.
5. Access to the team's Supabase project and GitHub repository.

Verify:

```bash
python --version
pip --version
git --version
```

On some systems the command is `python3` instead of `python`.

---

## 4. Step 1 — Create the backend project

Create a folder beside the frontend project:

```text
RideFlow/
  frontend/
  backend/
```

From the `backend` folder:

```bash
python -m venv .venv
```

Activate the virtual environment:

**Windows PowerShell**

```powershell
.venv\Scripts\Activate.ps1
```

**macOS/Linux**

```bash
source .venv/bin/activate
```

Create `requirements.txt`:

```text
fastapi[standard]
supabase
sqlalchemy>=2.0
psycopg[binary]
pydantic-settings
httpx
python-dotenv
pytest
```

Install packages:

```bash
pip install -r requirements.txt
```

After the first install, record the tested dependency versions for reproducible deployment:

```bash
pip freeze > requirements-lock.txt
```

For a small team, keep `requirements.txt` as the human-maintained dependency list and use the lock/export file consistently. Do not randomly upgrade dependencies immediately before the demo.

---

## 5. Step 2 — Create the backend folder structure

Use this modular structure:

```text
backend/
  app/
    __init__.py
    main.py
    core/
      __init__.py
      config.py
      security.py
      supabase_client.py
      errors.py
    db/
      __init__.py
      session.py
    api/
      __init__.py
      v1/
        __init__.py
        router.py
        endpoints/
          profiles.py
          drivers.py
          rides.py
          offers.py
          ride_history.py
          ride_events.py
    schemas/
      profile.py
      driver.py
      vehicle.py
      ride.py
      offer.py
      common.py
    services/
      profile_service.py
      driver_service.py
      location_service.py
      routing_service.py
      fare_service.py
      dispatch_service.py
      ride_service.py
      event_service.py
    repositories/
      profile_repository.py
      driver_repository.py
      ride_repository.py
      offer_repository.py
    workers/
      offer_expiry_worker.py
  migrations/
    001_extensions.sql
    002_tables.sql
    003_indexes_and_constraints.sql
    004_rls_and_realtime.sql
  tests/
    test_auth.py
    test_profiles.py
    test_dispatch.py
    test_ride_lifecycle.py
  .env
  .env.example
  .gitignore
  requirements.txt
```

You do not have to write every module immediately. Create them as you implement the corresponding feature.

**Meaning of the folders:**

- `api`: FastAPI route definitions and request/response handling.
- `schemas`: Pydantic models for validating incoming and outgoing JSON.
- `services`: business logic (fare, matching, dispatch, lifecycle).
- `repositories`: database reads/writes. Keep SQL queries out of route functions where possible.
- `db`: database engine and per-request database session.
- `core`: configuration, authentication and common errors.
- `workers`: background offer-expiry processing.
- `migrations`: versioned SQL changes committed to Git.
- `tests`: automated checks.

Do not put the whole backend in one `main.py` file.

---

## 6. Step 3 — Configure environment variables

Create `.env` in `backend/`:

```env
APP_ENV=development
API_PREFIX=/api/v1

SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co
SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY

DATABASE_URL=YOUR_SUPABASE_POSTGRES_CONNECTION_STRING

FRONTEND_ORIGINS=http://localhost:5173
ROUTING_BASE_URL=YOUR_ROUTING_PROVIDER_BASE_URL
ROUTING_API_KEY=

# Fare inputs are examples of configuration names, not agreed fare values.
FARE_BASE=SET_WITH_TEAM
FARE_PER_KM=SET_WITH_TEAM
FARE_OTHER_CHARGES=SET_WITH_TEAM

DRIVER_LOCATION_MAX_AGE_SECONDS=60
DRIVER_OFFER_TIMEOUT_SECONDS=20
DRIVER_SEARCH_RADIUS_METRES=5000
DRIVER_SHORTLIST_LIMIT=10
DRIVER_ROUTING_RANK_LIMIT=5
```

Create `.env.example` with the same variable names and placeholder values, but no secrets. Add `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/` and other generated files to `.gitignore`.

### Where credentials come from

- `SUPABASE_URL`: Supabase project URL.
- `SUPABASE_PUBLISHABLE_KEY`: Supabase publishable key. It is safe for the backend to use when calling the Auth API; do not confuse it with a service-role key.
- `DATABASE_URL`: copy the database connection string from Supabase Dashboard → **Connect**. Choose a connection mode appropriate for the hosting environment.
- `ROUTING_BASE_URL` and `ROUTING_API_KEY`: from the routing provider the team selects. Some providers do not require a key; do not hard-code a demo service URL as a production assumption.

### Supabase connection mode

Supabase offers direct, session-pooler and transaction-pooler connection strings. A persistent FastAPI server can use a direct connection when its network supports IPv6 (or the project's IPv4 add-on); if the deployment environment is IPv4-only, Supabase documents the shared **session pooler** as the alternative for a persistent backend. Copy the actual string from the Supabase Connect dialog and do not try to construct the pooler hostname yourself.

For SQLAlchemy with psycopg 3, the URL format is normally:

```text
postgresql+psycopg://USERNAME:PASSWORD@HOST:PORT/DATABASE
```

If the copied connection URL uses reserved characters in the password, URL-encode the password. Keep the whole connection string server-side.

---

## 7. Step 4 — Create the settings module

Create `app/core/config.py`:

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    supabase_url: str
    supabase_publishable_key: str
    database_url: str
    frontend_origins: str = "http://localhost:5173"
    routing_base_url: str
    routing_api_key: str | None = None
    fare_base: str
    fare_per_km: str
    fare_other_charges: str = "0"
    driver_location_max_age_seconds: int = 60
    driver_offer_timeout_seconds: int = 20
    driver_search_radius_metres: int = 5000
    driver_shortlist_limit: int = 10
    driver_routing_rank_limit: int = 5

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
```

The fare values above are intentionally configuration fields, not fixed prices. The team must decide and document fare policy before a real quote is exposed to users. Use Python `Decimal` for money rather than floating-point arithmetic.

---

## 8. Step 5 — Enable PostGIS and create the database schema

### 8.1 Enable PostGIS

In Supabase Dashboard:

1. Open **Database → Extensions**.
2. Find `postgis` and enable it.
3. Confirm which schema contains the extension before defining geography/geometry columns.

Supabase projects commonly install extensions in the `extensions` schema. If PostGIS is already installed in another schema, do not try to relocate it casually; use the actual schema name in your SQL. You can inspect it with:

```sql
select extname, extnamespace::regnamespace as extension_schema
from pg_extension
where extname = 'postgis';
```

The examples below assume PostGIS types/functions are accessible in the `extensions` schema. Adjust the schema qualifier if your project differs.

### 8.2 The eight V1 tables

Create these tables:

| Table | Purpose |
|---|---|
| `profiles` | Application profile linked to `auth.users` |
| `user_roles` | Passenger/driver/admin role assignments |
| `driver_profiles` | Verification and driver availability |
| `vehicles` | Vehicle data for a driver |
| `driver_locations` | One latest GPS location row per driver |
| `rides` | Passenger booking and ride lifecycle |
| `ride_offers` | Each sequential offer sent to a driver |
| `ride_events` | Append-only ride audit timeline |

The Supabase Auth `auth.users` table owns authentication identity. The `profiles.id` must use the same UUID as `auth.users.id`.

### 8.3 Create a SQL migration

Create `migrations/002_tables.sql`. The following is a compact V1 starting schema. Review names and constraints against the agreed API contract before running it.

```sql
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null,
  phone_number text,
  avatar_url text,
  account_status text not null default 'active'
    check (account_status in ('active', 'suspended', 'closed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.user_roles (
  user_id uuid not null references public.profiles(id) on delete cascade,
  role text not null check (role in ('passenger', 'driver', 'admin')),
  created_at timestamptz not null default now(),
  primary key (user_id, role)
);

create table if not exists public.driver_profiles (
  user_id uuid primary key references public.profiles(id) on delete cascade,
  verification_status text not null default 'pending'
    check (verification_status in ('pending', 'verified', 'rejected')),
  availability_status text not null default 'offline'
    check (availability_status in ('offline', 'available', 'busy')),
  last_became_available_at timestamptz,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.vehicles (
  id uuid primary key default gen_random_uuid(),
  driver_user_id uuid not null references public.driver_profiles(user_id) on delete cascade,
  vehicle_type text not null
    check (vehicle_type in ('bike', 'auto', 'car', 'accessible_van', 'other')),
  make_model text not null,
  registration_number text not null unique,
  seat_capacity integer not null check (seat_capacity >= 1),
  wheelchair_accessible boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.driver_locations (
  driver_user_id uuid primary key references public.driver_profiles(user_id) on delete cascade,
  location extensions.geography(Point, 4326) not null,
  accuracy_m double precision,
  heading_deg double precision,
  speed_mps double precision,
  device_recorded_at timestamptz,
  received_at timestamptz not null default now()
);

create table if not exists public.rides (
  id uuid primary key default gen_random_uuid(),
  passenger_id uuid not null references public.profiles(id),
  assigned_driver_id uuid references public.driver_profiles(user_id),
  assigned_vehicle_id uuid references public.vehicles(id),
  client_request_id uuid not null,
  status text not null default 'requested'
    check (status in ('requested', 'accepted', 'arriving', 'ongoing', 'completed', 'cancelled', 'no_driver_found')),
  pickup_location extensions.geography(Point, 4326) not null,
  pickup_address text not null,
  destination_location extensions.geography(Point, 4326) not null,
  destination_address text not null,
  estimated_distance_m integer,
  estimated_duration_s integer,
  final_distance_m integer,
  estimated_fare numeric(10,2),
  final_fare numeric(10,2),
  currency text not null default 'INR',
  fare_breakdown jsonb not null default '{}'::jsonb,
  route_geom extensions.geometry(LineString, 4326),
  requested_at timestamptz not null default now(),
  accepted_at timestamptz,
  arrived_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  cancelled_at timestamptz,
  cancelled_by text check (cancelled_by in ('passenger', 'driver', 'admin', 'system')),
  cancellation_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (passenger_id, client_request_id)
);

create table if not exists public.ride_offers (
  id uuid primary key default gen_random_uuid(),
  ride_id uuid not null references public.rides(id) on delete cascade,
  driver_user_id uuid not null references public.driver_profiles(user_id),
  offer_number integer not null,
  status text not null default 'offered'
    check (status in ('offered', 'accepted', 'rejected', 'expired', 'withdrawn')),
  pickup_distance_m integer,
  pickup_eta_s integer,
  match_score numeric(8,3),
  match_factors jsonb not null default '{}'::jsonb,
  rejection_reason text,
  offered_at timestamptz not null default now(),
  expires_at timestamptz not null,
  responded_at timestamptz,
  unique (ride_id, driver_user_id),
  unique (ride_id, offer_number)
);

create table if not exists public.ride_events (
  id uuid primary key default gen_random_uuid(),
  ride_id uuid not null references public.rides(id) on delete cascade,
  actor_user_id uuid references public.profiles(id),
  actor_type text not null check (actor_type in ('passenger', 'driver', 'admin', 'system')),
  event_type text not null,
  from_status text,
  to_status text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
```

**Schema notes:**

- If `gen_random_uuid()` is unavailable in your environment, enable the required UUID extension or generate UUIDs in Python.
- If PostGIS is in `public` rather than `extensions`, change `extensions.geography` and `extensions.geometry` accordingly.
- Keep the API field names and database column mappings consistent. It is acceptable for the database column names to differ if the repository layer maps them explicitly.
- Use `Decimal` values in Python and return fare values as decimal strings, for example `"750.00"`.

### 8.4 Create indexes and concurrency constraints

Create `migrations/003_indexes_and_constraints.sql`:

```sql
create index if not exists idx_driver_locations_geog
  on public.driver_locations using gist (location);

create index if not exists idx_driver_profiles_match
  on public.driver_profiles (verification_status, availability_status);

create index if not exists idx_vehicles_driver_active
  on public.vehicles (driver_user_id, is_active);

create unique index if not exists one_active_vehicle_per_driver
  on public.vehicles (driver_user_id)
  where is_active = true;

create index if not exists idx_rides_passenger_requested
  on public.rides (passenger_id, requested_at desc);

create index if not exists idx_rides_assigned_driver
  on public.rides (assigned_driver_id, status);

create unique index if not exists one_active_ride_per_driver
  on public.rides (assigned_driver_id)
  where assigned_driver_id is not null
    and status in ('accepted', 'arriving', 'ongoing');

create unique index if not exists one_open_offer_per_ride
  on public.ride_offers (ride_id)
  where status = 'offered';

create unique index if not exists one_open_offer_per_driver
  on public.ride_offers (driver_user_id)
  where status = 'offered';

create index if not exists idx_ride_offers_expiry
  on public.ride_offers (expires_at)
  where status = 'offered';

create index if not exists idx_ride_events_ride_created
  on public.ride_events (ride_id, created_at);
```

The indexes are important. They prevent two live offers for the same ride/driver and prevent one driver from being assigned two active rides even if two requests arrive at almost the same time.

### 8.5 Keep migrations in Git

For the first hackathon pass, it is acceptable to run reviewed SQL files through Supabase SQL Editor. Save those same SQL files in `backend/migrations/` and commit them. For every later schema change, create a new numbered migration; do not edit a migration that has already been applied in a shared database. This gives both developers one reliable database history.

---

## 9. Step 6 — Create the database connection

Create `app/db/session.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Use a single SQLAlchemy session per request. Do not create a new engine for every API call. Keep database transactions short; do not hold a database row lock while waiting for a slow routing API response.

The exact pool size must fit the Supabase plan's connection limits and the number of running backend instances. Start small for the hackathon.

---

## 10. Step 7 — Verify Supabase access tokens

### Recommended beginner-friendly V1 method

Supabase's Python `auth.get_user(jwt)` method retrieves the user from Supabase Auth and validates the access-token JWT on the server. Use it in a FastAPI dependency. This introduces an Auth-server request, but it keeps the initial implementation straightforward and avoids copying a shared JWT secret into the backend.

Create a server-side Supabase Auth client with `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (not the service-role key):

```python
from supabase import create_client
from app.core.config import settings

supabase_auth = create_client(
    settings.supabase_url,
    settings.supabase_publishable_key,
)
```

Create `app/core/security.py`:

```python
from dataclasses import dataclass
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core.supabase_client import supabase_auth

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: str
    email: str | None = None


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Missing bearer token',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    try:
        response = supabase_auth.auth.get_user(credentials.credentials)
        user = response.user
        if user is None:
            raise ValueError('No user returned')
        return CurrentUser(id=str(user.id), email=user.email)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or expired access token',
            headers={'WWW-Authenticate': 'Bearer'},
        )
```

Put the client creation shown above in `app/core/supabase_client.py` and import it from `security.py` so it is created once. In production, log the underlying validation exception safely on the server, but do not return token contents or internal exception details to the caller. Distinguish an Auth server rejection (401) from a temporary Auth-server/network failure (503); the compact example maps all verification exceptions to 401 only to keep the starter dependency easy to read.

### Later performance option: verify with JWKS

Supabase provides a JWKS endpoint for projects using asymmetric signing keys. A production optimisation is to verify the signature locally with a well-maintained JWT library, while validating the issuer, expiry, audience and allowed signing algorithm. Confirm the project's actual signing-key type first. Supabase documents that legacy shared-secret HS256 projects should generally verify with the Auth server; do not casually copy the legacy JWT secret into multiple services.

### Identify the application role

After token verification, use the verified `user.id` to query `user_roles`. Do not accept a role from the request body or query string as proof of permission.

Create role dependencies such as `require_passenger`, `require_driver`, and `require_admin`. A driver must also have `verification_status = 'verified'` before using availability/location/offer endpoints that require a verified driver.

---

## 11. Step 8 — Define Pydantic schemas

Create separate request/response schemas instead of accepting arbitrary dictionaries. Example `app/schemas/ride.py`:

```python
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class CoordinateInput(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    address: str = Field(min_length=1, max_length=500)


class RideEstimateRequest(BaseModel):
    pickup: CoordinateInput
    destination: CoordinateInput
    vehicle_type: Literal['bike', 'auto', 'car', 'accessible_van', 'other']


class CreateRideRequest(RideEstimateRequest):
    client_request_id: UUID


class RideEstimateResponse(BaseModel):
    distance_m: int
    duration_s: int
    estimated_fare: str
    currency: str = 'INR'
    fare_breakdown: dict
    route: dict
```

Use Pydantic schemas for every endpoint. Validate coordinate ranges, IDs, enum choices, pagination, text lengths and numeric limits before calling the service layer.

### API response conventions

The agreed API contract uses:

**Success**

```json
{
  "data": {
    "example": "endpoint-specific result"
  }
}
```

**Error**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A readable explanation",
    "details": {},
    "request_id": "request-id"
  }
}
```

Use ISO 8601 UTC timestamps, metres for distance, seconds for duration, coordinates as `{lat, lng}`, and decimal strings for fares in INR. GeoJSON `LineString.coordinates` are `[longitude, latitude]` pairs.

Use a consistent exception handler so FastAPI validation errors and application errors are converted into the agreed envelope. Do not expose stack traces, SQL statements or secret values in responses.

---

## 12. Step 9 — Configure FastAPI, API versioning and CORS

Create `app/api/v1/router.py` and include routers for profiles, drivers, rides, offers, history and events. Each router should contain a small set of related endpoints.

Create `app/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.router import api_router

app = FastAPI(title='RideFlow API', version='1.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type', 'Accept'],
)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get('/health')
def health_check():
    return {'status': 'ok'}
```

`allow_origins` must contain the exact frontend origins, not `*` in production. Add the local Vite origin during development and the deployed Vercel domain after deployment. The `/health` endpoint is an operational health check; it is not one of the 20 public business endpoints.

Create `app/api/v1/router.py`:

```python
from fastapi import APIRouter
from app.api.v1.endpoints import profiles, drivers, rides, offers, ride_history, ride_events

api_router = APIRouter()
api_router.include_router(profiles.router, tags=['profiles'])
api_router.include_router(drivers.router, tags=['drivers'])
api_router.include_router(rides.router, tags=['rides'])
api_router.include_router(offers.router, tags=['offers'])
api_router.include_router(ride_history.router, tags=['ride history'])
api_router.include_router(ride_events.router, tags=['ride events'])
```

Run locally:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` to use FastAPI's interactive API documentation. Do not expose the development docs publicly without considering your production security needs.

---

## 13. Step 10 — Implement the APIs in dependency order

Build and test endpoints in the following order. Do not attempt to implement all 20 in one file or in one large commit.

### Group A — Profile and driver setup

**1. `PUT /api/v1/me/profile`**

- Authenticate the caller.
- Create/update `profiles` using the authenticated user's ID.
- Add the passenger role if this is the passenger onboarding path.
- Return profile ID, name, phone, roles and account status.
- Never accept `id` from the body as the owner ID.

**2. `GET /api/v1/me`**

- Authenticate the caller.
- Read their `profiles` row and `user_roles`.
- Return profile and roles.

**3. `POST /api/v1/drivers/me/onboarding`**

- Authenticate the caller.
- Validate vehicle details and registration number.
- Create `driver_profiles` with `verification_status = 'pending'` and `availability_status = 'offline'`.
- Create the active `vehicles` row.
- Add the driver role in `user_roles`.
- Perform inserts in a transaction; roll back all if one fails.
- Do not let the caller set verification to verified.

**4. `GET /api/v1/drivers/me`**

- Require driver role.
- Return the driver profile, verification/availability state and active vehicle.

### Group B — Availability and GPS

**5. `PATCH /api/v1/drivers/me/availability`**

- Require driver role and verified status.
- Accept only `available` or `offline` from the client.
- Reject going offline while an active ride exists unless the agreed cancellation/ride flow is used.
- The backend sets `busy` when a driver accepts an offer; the frontend cannot request `busy`.
- Update `last_became_available_at` when driver transitions online.

**6. `POST /api/v1/drivers/me/location`**

- Require a verified driver.
- Validate `lat`, `lng`, accuracy, heading, speed and device timestamp.
- Convert `{lat, lng}` to PostGIS point using longitude first:

```sql
ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::extensions.geography
```

- Upsert one row in `driver_locations` keyed by `driver_user_id`; do not append a row for every ping.
- Set `received_at = now()` on the server. Preserve `device_recorded_at` for diagnostics.
- Reject impossible coordinates and stale/future timestamps according to agreed validation tolerances.

### Group C — Map shortlist, routing and booking

**7. `GET /api/v1/drivers/nearby?lat=&lng=&radius_m=`**

- Require passenger role.
- Use PostGIS to search within the radius.
- Filter verified drivers, `availability_status = 'available'`, fresh GPS, suitable active vehicle, and no active ride.
- Return approximate positions only. Never return raw exact GPS to unrelated passengers.

**8. `POST /api/v1/rides/estimate`**

- Require passenger role.
- Validate pickup, destination and vehicle type.
- Call the routing provider for road distance, road duration and route geometry.
- Calculate estimated fare in the backend.
- Return distance in metres, duration in seconds, fare as a decimal string, fare breakdown and route LineString.

**9. `POST /api/v1/rides`**

- Require passenger role.
- Require `client_request_id`.
- Recalculate/validate the route and fare on the backend; do not trust a fare submitted by React.
- Insert the ride with status `requested`.
- Trigger sequential dispatch.
- Return ride and dispatch status.
- If the same passenger repeats the same `client_request_id`, return the existing booking rather than inserting a duplicate.

### Group D — Driver offers

**10. `GET /api/v1/drivers/me/offers?status=offered`**

- Require driver role.
- Return only offers belonging to the authenticated driver.
- Include pickup/destination, pickup distance/ETA, estimated fare and expiry.

**11. `POST /api/v1/driver/offers/{offer_id}/accept`**

- Require the driver who owns the offer.
- Lock/check the offer and ride in a transaction.
- Reject if the offer expired, was already responded to, or the driver is no longer eligible.
- Confirm no other active ride is assigned to the driver.
- Mark offer accepted, assign driver/vehicle to ride, set driver busy, create event, commit atomically.

**12. `POST /api/v1/driver/offers/{offer_id}/reject`**

- Require the driver who owns the offer.
- Validate reason: `too_far`, `busy`, `personal_reason`, `vehicle_issue`, or `other`.
- Mark offer rejected and add event.
- Create the next eligible driver's offer if any remain.
- If no candidate remains, set ride to `no_driver_found`.

### Group E — Ride detail, status, history and cancellation

**13. `GET /api/v1/rides/{ride_id}`**

- Allow only the booking passenger, currently assigned driver or authorised admin.
- Return pickup, destination, state, estimate, and permitted assigned-driver data.
- Return exact live driver location only when the caller is the passenger on that active assigned ride and policy allows it.

**14. `GET /api/v1/me/rides?role=passenger|driver&limit=&cursor=`**

- Require authentication.
- Only permit a role the current user actually has.
- Return paginated history belonging to the current user.

**15. `POST /api/v1/driver/rides/{ride_id}/arrived`**

- Require the assigned driver.
- Permit only the valid pre-journey state transition.
- Set status `arriving`, timestamp and event.

**16. `POST /api/v1/driver/rides/{ride_id}/start`**

- Require the assigned driver.
- Permit only after the driver has arrived.
- Set status `ongoing`, timestamp and event.

**17. `POST /api/v1/driver/rides/{ride_id}/complete`**

- Require the assigned driver.
- Permit only for an ongoing ride.
- Calculate final distance/fare on the backend using trusted trip data and the agreed pricing rules.
- Set status `completed`, final fare and timestamp; release driver to available or agreed post-trip state.
- Never accept a final fare value from the browser.

**18. `POST /api/v1/rides/{ride_id}/cancel`**

- Require the passenger who booked the ride.
- Validate cancellation is allowed for the ride's current state.
- Mark cancelled, withdraw any open offer, release an assigned driver where appropriate, write event.

**19. `POST /api/v1/driver/rides/{ride_id}/cancel`**

- Require the assigned driver.
- Allow reassignment only before the journey starts.
- Release previous driver, return ride to requested/searching and create next offer.
- Do not silently reassign an in-progress ride.

**20. `GET /api/v1/rides/{ride_id}/events`**

- Require booking passenger, assigned driver or authorised admin.
- Return the append-only event timeline in chronological order.

---

## 14. Step 11 — Implement PostGIS driver matching

The matching process should have two stages.

### Stage 1: geographic shortlist

Use PostGIS to shortlist nearby drivers. A representative query is:

```sql
select
  dp.user_id as driver_user_id,
  v.id as vehicle_id,
  v.vehicle_type,
  extensions.st_distance(
    dl.location,
    extensions.st_setsrid(extensions.st_makepoint(:lng, :lat), 4326)::extensions.geography
  ) as straight_line_distance_m
from public.driver_profiles dp
join public.driver_locations dl on dl.driver_user_id = dp.user_id
join public.vehicles v on v.driver_user_id = dp.user_id and v.is_active = true
where dp.verification_status = 'verified'
  and dp.availability_status = 'available'
  and v.vehicle_type = :vehicle_type
  and dl.received_at >= now() - (:max_age_seconds * interval '1 second')
  and extensions.st_dwithin(
    dl.location,
    extensions.st_setsrid(extensions.st_makepoint(:lng, :lat), 4326)::extensions.geography,
    :radius_m
  )
  and not exists (
    select 1 from public.rides r
    where r.assigned_driver_id = dp.user_id
      and r.status in ('accepted', 'arriving', 'ongoing')
  )
  and not exists (
    select 1 from public.ride_offers current_offer
    where current_offer.driver_user_id = dp.user_id
      and current_offer.status = 'offered'
  )
  and not exists (
    select 1 from public.ride_offers previous_offer
    where previous_offer.ride_id = :ride_id
      and previous_offer.driver_user_id = dp.user_id
  )
order by straight_line_distance_m asc
limit :shortlist_limit;
```

This is a shortlist only. Straight-line distance is not the final pickup ETA. Confirm PostGIS function names/schema against the schema in which your extension is installed.

### Stage 2: road ETA ranking

For the shortlist (initially top 3–5 candidates), call the selected OSRM-compatible routing provider to get road distance and road duration from driver to pickup. Rank candidates by pickup ETA. Use time since last became available as a tie-breaker if needed. Keep provider calls outside long database transactions.

Store the chosen match factors on the offer (`pickup_distance_m`, `pickup_eta_s`, `match_score`, `match_factors`) for debugging and demo explanation.

### Candidate eligibility rules

A driver is eligible only when:

- verification status is `verified`;
- availability status is `available`;
- the latest GPS update is fresh (use a configured max age, initially around 60 seconds for V1 and tune it);
- the driver has an active vehicle matching the requested vehicle type;
- there is no active ride assigned to the driver;
- the driver has not already received an offer for this ride.

When the passenger's booking is for a selected vehicle type, never send it to another vehicle type unless the product rules explicitly permit that.

---

## 15. Step 12 — Implement sequential driver dispatch

This is one of the most important backend features. Dispatch one offer at a time, not to all drivers at once.

### First offer

1. Create ride with status `requested`.
2. Find geographically eligible drivers.
3. Rank a shortlist by road ETA.
4. Select the highest-ranked candidate not previously offered this ride.
5. Insert a `ride_offers` row with status `offered`, incremented offer number, `offered_at = now()` and `expires_at = now() + configured timeout`.
6. Initial timeout can be 15–20 seconds and should be configurable.
7. Notify the driver through Realtime.

### On driver rejection

1. Lock the current offer and associated ride.
2. Confirm offer is still `offered` and belongs to this driver.
3. Set offer `rejected`, `responded_at`, and rejection reason.
4. Add a `driver_rejected` ride event.
5. Select the next eligible driver who has not previously been offered this ride.
6. Create the next offer and notify that driver.
7. If no candidate remains, set ride to `no_driver_found` and add a system event.

### On offer timeout

A background worker must find offers where status is `offered` and `expires_at <= now()`:

1. Claim the expired offer row with a transaction and row lock (use `FOR UPDATE SKIP LOCKED` or an equivalent atomic claim).
2. Re-check that it is still offered and expired.
3. Mark it `expired` and add an event.
4. Create an offer for the next eligible driver.
5. If candidates are exhausted, set `no_driver_found`.

The worker must be safe to run more than once. Database constraints and row locks must prevent the same expired offer from being processed twice.

### Accept an offer atomically

Use a single transaction. The transaction should:

1. Lock offer and ride rows.
2. Confirm offer is still offered and not expired.
3. Confirm authenticated driver owns the offer.
4. Confirm driver is verified and available.
5. Confirm driver does not already have an active ride.
6. Mark offer accepted.
7. Assign driver and vehicle to ride; set ride status `accepted`.
8. Set driver availability to `busy`.
9. Add `driver_accepted` ride event.
10. Commit.

If any condition fails, roll back and return the correct conflict error. The partial unique index on active rides is the final safety check against double assignment.

### Worker implementation for V1

For a single-instance hackathon deployment, run a small expiry worker with the FastAPI application's lifespan or run it as a separate worker process using the same repository. The worker must query the database for expired offers on every pass; do not keep its queue only in Python memory. A database restart or application restart must not lose pending expiries.

If using FastAPI lifespan, use the modern `lifespan` context-manager pattern to start and stop the task cleanly. Start with one Uvicorn worker/one backend instance for the demo, and keep the DB claim transaction-safe. If the backend is later scaled to multiple instances, retain `SKIP LOCKED`/atomic updates and consider a dedicated worker service. Do not use an unprotected in-memory timer per request; it can fire twice or disappear on restart.

---

## 16. Step 13 — Implement ride state and event history

Allowed V1 ride states:

```text
requested -> accepted -> arriving -> ongoing -> completed
     |            |          |
     |            |          +----> cancelled (when policy allows)
     |            +--------------- > cancelled (when policy allows)
     +----> no_driver_found
     +----> cancelled
```

The exact cancellation rules must be enforced in the backend. Never let the client send an arbitrary `status` and update the database directly.

For each meaningful action, insert a `ride_events` row:

- `ride_requested`
- `driver_offered`
- `driver_rejected`
- `offer_expired`
- `driver_accepted`
- `driver_arrived`
- `ride_started`
- `ride_completed`
- `passenger_cancelled`
- `driver_cancelled`
- `no_driver_found`

Store who acted (`actor_user_id`, `actor_type`), previous/next status where applicable, metadata and timestamp. Treat this as append-only; do not edit old event rows to change history.

---

## 17. Step 14 — Routing and fare calculation

### Routing provider wrapper

Create `app/services/routing_service.py`. The rest of the app should call a function such as:

```python
def get_route(origin_lat: float, origin_lng: float, destination_lat: float, destination_lng: float):
    """Return road distance (m), duration (s), and GeoJSON LineString."""
    ...
```

Keep all provider-specific URL formats and response mapping inside this module. Use a timeout, handle non-2xx responses, validate the returned route, and map provider failure to `502 ROUTING_ERROR`. Do not let frontend code call OSRM directly.

### Fare service

Create `app/services/fare_service.py`:

- Read rates from configuration or a database fare table.
- Use road distance (not straight-line distance) as an input.
- Use `Decimal` for all money arithmetic.
- Return a `fare_breakdown` with base, distance and other charges if those components are part of the agreed pricing policy.
- Return a string with two decimal places and currency `INR`.
- Recalculate final fare on ride completion from trusted final distance/trip data.

The example fare values in the API contract are illustrative, not agreed prices. The team must decide base fare, per-kilometre rate, minimum fare, waiting/other charges, rounding and cancellation rules before launch. Never accept `estimated_fare` or `final_fare` from the browser as authoritative.

**Final-distance limitation in the current schema:** `driver_locations` stores only the driver's latest location; it does not store a ride GPS trail. Therefore the current V1 schema cannot independently reconstruct the exact driven path. For the hackathon V1, agree that final distance/fare is route-based (calculated again by the backend from the trip's pickup/destination and the chosen routing provider), or add a separate ride-location-points/history table if actual travelled distance is required. Do not describe route-based distance as odometer-accurate distance.

### Provider availability

Do not build production around an undocumented/free demo routing server. Select a routing provider or host an OSRM-compatible service, check its usage limits and configure the base URL/credentials in the backend environment. If the provider is unavailable, return `ROUTING_ERROR` and let the frontend offer a retry.

---

## 18. Step 15 — Configure Supabase RLS and Realtime

All tables in exposed schemas should have Row Level Security enabled. RLS protects browser/Data API and Realtime access; it does **not** replace FastAPI's own authorization checks. A direct database connection using an owner/privileged role may bypass RLS, so FastAPI must explicitly filter every query by authenticated user and allowed role.

### 18.1 Enable RLS

Run for all eight tables:

```sql
alter table public.profiles enable row level security;
alter table public.user_roles enable row level security;
alter table public.driver_profiles enable row level security;
alter table public.vehicles enable row level security;
alter table public.driver_locations enable row level security;
alter table public.rides enable row level security;
alter table public.ride_offers enable row level security;
alter table public.ride_events enable row level security;
```

### 18.2 Example SELECT policies for Realtime

These examples are starting points. Review the exact business rules, grants and column visibility before enabling them in your project.

```sql
-- A user can read their own profile.
create policy "profile owner can read profile"
on public.profiles for select to authenticated
using (id = (select auth.uid()));

-- A user can read their own role rows.
create policy "user can read own roles"
on public.user_roles for select to authenticated
using (user_id = (select auth.uid()));

-- A driver can read their own driver profile.
create policy "driver can read own profile"
on public.driver_profiles for select to authenticated
using (user_id = (select auth.uid()));

-- A user can read rides they booked or are assigned to.
create policy "ride participants can read ride"
on public.rides for select to authenticated
using (
  passenger_id = (select auth.uid())
  or assigned_driver_id = (select auth.uid())
);

-- A driver can read only their own offers.
create policy "driver can read own offers"
on public.ride_offers for select to authenticated
using (driver_user_id = (select auth.uid()));

-- Passenger can read location only for the driver assigned to their active ride.
create policy "driver can read own location and passenger can read assigned active driver"
on public.driver_locations for select to authenticated
using (
  driver_user_id = (select auth.uid())
  or exists (
    select 1 from public.rides r
    where r.assigned_driver_id = driver_locations.driver_user_id
      and r.passenger_id = (select auth.uid())
      and r.status in ('accepted', 'arriving', 'ongoing')
  )
);

-- Participants can read events belonging to their ride.
create policy "ride participants can read ride events"
on public.ride_events for select to authenticated
using (
  exists (
    select 1 from public.rides r
    where r.id = ride_events.ride_id
      and (r.passenger_id = (select auth.uid()) or r.assigned_driver_id = (select auth.uid()))
  )
);
```

These are SELECT policies for reading/realtime. Do not add broad browser-side INSERT/UPDATE policies for ride state or GPS just to make a demo work. Keep business writes through FastAPI. Add narrowly scoped write policies only if the project deliberately exposes that operation to the browser.

Also check table grants. On an exposed schema, RLS policies and SQL grants work together: a policy does not remove existing grants. Revoke privileges that are not required.

### 18.3 Add tables to Realtime publication

The V1 frontend expects changes on `rides`, `ride_offers`, `ride_events` and `driver_locations`. Enable the tables for Supabase Realtime Postgres Changes in the dashboard/publication. For example, where the tables are not already included:

```sql
alter publication supabase_realtime add table public.rides;
alter publication supabase_realtime add table public.ride_offers;
alter publication supabase_realtime add table public.ride_events;
alter publication supabase_realtime add table public.driver_locations;
```

Check the publication before running repeated `ALTER PUBLICATION` statements; adding a table that is already present can produce an error.

### 18.4 Postgres Changes or Broadcast?

The V1 contract specifies Postgres Changes because it is straightforward for a low-volume hackathon. Supabase's current documentation recommends Broadcast for scalability and security, and describes Postgres Changes as simpler but less scalable. Keep notifications isolated so a later change to Broadcast does not require rewriting ride business logic.

---

## 19. Step 16 — Add the 20 API routes and response behaviour

Base path: `/api/v1`  
Content type: `application/json`  
Authentication: `Authorization: Bearer <Supabase access token>` except health check and Supabase Auth endpoints.

| # | Method and path | Required identity / action |
|---:|---|---|
| 1 | `PUT /me/profile` | Authenticated user creates/updates own profile |
| 2 | `GET /me` | Authenticated user loads own profile and roles |
| 3 | `POST /drivers/me/onboarding` | Authenticated user creates driver profile + vehicle |
| 4 | `GET /drivers/me` | Driver loads own driver profile |
| 5 | `PATCH /drivers/me/availability` | Verified driver requests available/offline |
| 6 | `POST /drivers/me/location` | Verified driver submits latest GPS |
| 7 | `GET /drivers/nearby` | Passenger sees approximate nearby drivers |
| 8 | `POST /rides/estimate` | Passenger gets route, ETA, estimated fare |
| 9 | `POST /rides` | Passenger creates ride and starts dispatch |
| 10 | `GET /drivers/me/offers` | Driver loads own incoming offers |
| 11 | `POST /driver/offers/{offer_id}/accept` | Offer-owning driver accepts offer |
| 12 | `POST /driver/offers/{offer_id}/reject` | Offer-owning driver rejects offer |
| 13 | `GET /rides/{ride_id}` | Passenger/assigned driver/admin loads authorised ride |
| 14 | `GET /me/rides` | Passenger or driver loads own history |
| 15 | `POST /driver/rides/{ride_id}/arrived` | Assigned driver marks arrival |
| 16 | `POST /driver/rides/{ride_id}/start` | Assigned driver starts journey |
| 17 | `POST /driver/rides/{ride_id}/complete` | Assigned driver completes; backend calculates fare |
| 18 | `POST /rides/{ride_id}/cancel` | Booking passenger cancels when allowed |
| 19 | `POST /driver/rides/{ride_id}/cancel` | Assigned driver cancels before journey starts |
| 20 | `GET /rides/{ride_id}/events` | Ride participant/admin loads authorised timeline |

For the exact body and response JSON for each endpoint, use the separate **RideFlow V1 API Contract**. Implement the route path and method exactly as specified there. The frontend and backend must not create separate undocumented versions of the contract.

### Common error codes

| HTTP | Code | Use |
|---:|---|---|
| 400 | `VALIDATION_ERROR` | Invalid input |
| 401 | `UNAUTHORIZED` | Missing/invalid/expired token |
| 403 | `FORBIDDEN` | Authenticated user lacks required role/permission |
| 404 | `NOT_FOUND` | Resource does not exist or is not visible |
| 409 | `RIDE_STATE_CONFLICT` | Invalid transition or concurrent change |
| 409 | `OFFER_EXPIRED` | Offer expired before response |
| 409 | `DRIVER_UNAVAILABLE` | Driver no longer eligible |
| 429 | `RATE_LIMITED` | Too many requests |
| 502 | `ROUTING_ERROR` | Routing provider failure |
| 503 | `SERVICE_UNAVAILABLE` | Required dependency unavailable |

Return the agreed error envelope with a request ID. Create a request ID for each request (or use a trusted proxy-provided one) and include it in server logs.

---

## 20. Step 17 — Testing strategy

Test the backend before the frontend developer begins full integration. The frontend developer needs stable endpoints, status codes and response shapes.

### Unit tests

Test these functions without calling external services:

- Fare calculation and rounding.
- Vehicle-type validation.
- Driver candidate filtering.
- Offer timeout calculation.
- Ride state transition rules.
- Role and permission checks.
- Conversion between `{lat, lng}` and PostGIS `[lng, lat]` point order.

### Integration tests

Use a separate test Supabase project or isolated test database, not live production data.

- Profile create/update and role assignment.
- Driver onboarding starts pending/offline.
- Unverified driver cannot go online.
- GPS location upserts one row per driver.
- Nearby query filters stale/unverified/busy/wrong-vehicle drivers.
- Estimate uses road route and returns expected schema.
- Duplicate `client_request_id` does not create two rides.
- Rejection creates the next sequential offer.
- Offer timeout creates the next sequential offer exactly once.
- Only one open offer exists per ride and driver.
- Two concurrent accept requests cannot assign two drivers.
- One driver cannot be assigned two active rides.
- Passenger cannot read another passenger's ride.
- Driver cannot accept an offer belonging to another driver.
- Driver cannot complete a ride that is not ongoing.
- In-progress driver cancellation does not silently reassign the ride.
- Realtime subscribers only see authorised rows.

### API manual tests

Open:

```text
http://127.0.0.1:8000/docs
```

Use the frontend developer's test accounts or send a valid Supabase access token in the Authorize control. Test success, invalid token, wrong role, expired offer, and invalid ride-state paths.

### Required concurrency tests

These are mandatory because rides and offers can be affected by multiple requests at almost the same time:

1. Driver presses Accept twice quickly.
2. Two drivers attempt to accept the same ride offer (including replay attempts).
3. Offer expiry worker and driver Accept happen at the same time.
4. Passenger retries `POST /rides` after a client timeout.
5. Driver is offered two rides at the same time.

Database constraints and transactions must ensure the final state is valid even if the requests race.

---

## 21. Step 18 — Run the backend locally and connect the frontend

Start FastAPI from the `backend` folder with `.venv` activated:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check:

- `GET http://127.0.0.1:8000/health` returns a healthy response.
- `http://127.0.0.1:8000/docs` loads.
- Authenticated requests return the agreed envelope.
- CORS allows `http://localhost:5173`.
- Database connection uses the intended Supabase project.
- Route estimates work against the selected routing provider.

Give the frontend developer:

- API base URL: `http://127.0.0.1:8000/api/v1`.
- Supabase project URL and publishable key (the latter is safe for browser configuration).
- Passenger and verified-driver test accounts.
- API docs URL.
- Exact Realtime/RLS status and the fields used by subscriptions.

Never give the frontend developer the database password or service-role key.

---

## 22. Step 19 — Deploy FastAPI to Render

This guide uses Render as the straightforward V1 backend hosting option. Supabase continues to host the database and Auth; Vercel hosts the React frontend.

### 22.1 Prepare the repository

Push the backend code and migration scripts to GitHub. If frontend and backend are in one repository, make sure the Render service's Root Directory is set to `backend`.

Confirm these files exist:

- `requirements.txt`
- `app/main.py`
- `.gitignore`
- migration SQL files committed in `migrations/`

Do not commit `.env`.

### 22.2 Create a Render Web Service

1. Sign in to Render.
2. Choose **New → Web Service**.
3. Connect the GitHub repository.
4. Set Root Directory to `backend` if using a monorepo.
5. Runtime: Python.
6. Build command:

```bash
pip install -r requirements.txt
```

7. Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

8. Set the health check path to `/health` if available in the service settings.
9. Create the service and check its deploy logs.

### 22.3 Set backend environment variables in Render

Add every required variable from `.env`, using production values:

```text
APP_ENV=production
API_PREFIX=/api/v1
SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co
SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY
DATABASE_URL=YOUR_PRODUCTION_DATABASE_CONNECTION_STRING
FRONTEND_ORIGINS=https://YOUR-APP.vercel.app
ROUTING_BASE_URL=YOUR_PRODUCTION_ROUTING_PROVIDER_URL
ROUTING_API_KEY=YOUR_SERVER_SIDE_ROUTING_KEY_IF_REQUIRED
FARE_BASE=TEAM_DECIDED_VALUE
FARE_PER_KM=TEAM_DECIDED_VALUE
FARE_OTHER_CHARGES=TEAM_DECIDED_VALUE
DRIVER_LOCATION_MAX_AGE_SECONDS=60
DRIVER_OFFER_TIMEOUT_SECONDS=20
DRIVER_SEARCH_RADIUS_METRES=5000
DRIVER_SHORTLIST_LIMIT=10
DRIVER_ROUTING_RANK_LIMIT=5
```

Do not copy the local development `.env` file into GitHub. Add values through Render's environment settings.

### 22.4 Verify deployed backend

Open the Render service URL:

```text
https://YOUR-SERVICE.onrender.com/health
```

Check that it responds. Then open `/docs` if enabled and test a protected endpoint using a valid test user's access token. Check Render logs for database errors, auth errors, routing provider failures and worker errors.

### 22.5 Connect Vercel frontend

Give the frontend developer the production API base URL:

```text
https://YOUR-SERVICE.onrender.com/api/v1
```

The frontend developer must set this as `VITE_API_BASE_URL` in Vercel. Confirm the exact Vercel origin is in `FRONTEND_ORIGINS` on Render. If the frontend's domain changes, update the CORS environment variable and redeploy the backend.

---

## 23. Step 20 — Production security checklist

- [ ] FastAPI verifies every Supabase access token.
- [ ] Every endpoint obtains user identity from the verified token, never from a request-supplied user ID.
- [ ] Application roles are loaded from `user_roles`.
- [ ] Every endpoint checks the correct owner/role/ride relationship.
- [ ] Only verified drivers can go online or receive offers.
- [ ] Passenger responses expose approximate nearby driver positions only.
- [ ] Exact driver location is available only to the assigned passenger during an authorised active ride.
- [ ] All eight exposed tables have RLS enabled and grants reviewed.
- [ ] Realtime publication includes only required tables and RLS restricts each subscriber.
- [ ] Frontend never receives database credentials or service-role keys.
- [ ] Backend secrets exist only in deployment environment settings.
- [ ] Database connection uses SSL and the correct Supabase connection mode.
- [ ] All fare calculations run in the backend and use `Decimal`.
- [ ] Accept/reject/expiry/dispatch are transaction-safe and idempotent.
- [ ] Active-ride and one-open-offer unique indexes are in place.
- [ ] CORS allows exact frontend origins only.
- [ ] Production API is HTTPS.
- [ ] Error responses do not leak stack traces, secrets or SQL details.
- [ ] Logs include request IDs but never log access tokens or passwords.

---

## 24. Recommended backend work order

| Phase | Work | Completion proof |
|---:|---|---|
| 1 | Create FastAPI project, config, health route, GitHub repo | API runs locally and `/health` works |
| 2 | Create Supabase project settings, PostGIS, migrations and eight tables | Schema and indexes exist |
| 3 | Database session + token verification + role dependencies | Protected `/me` works with valid token and returns 401 without token |
| 4 | Profiles, driver onboarding, vehicles and availability | Passenger and driver profiles can be created/read |
| 5 | GPS upsert and PostGIS nearby query | Latest location updates and nearby filtering works |
| 6 | Routing wrapper and fare service | Estimate endpoint returns road distance, ETA, route and fare |
| 7 | Ride creation, idempotency and sequential dispatch | One booking creates one offer at a time |
| 8 | Accept/reject/expiry worker and concurrency constraints | No double assignment; expired/rejected offer advances correctly |
| 9 | Ride lifecycle, cancellations, history and events | Complete ride is stored and queryable |
| 10 | RLS, Realtime publication and integration testing | Correct users receive only authorised notifications/data |
| 11 | Deploy Render service and connect Vercel | End-to-end ride completes on production URLs |

### Definition of done for the backend developer

The backend is ready for the V1 demo when:

- All 20 API endpoints match the API contract.
- Supabase tokens are verified and application roles are enforced.
- PostGIS finds fresh, eligible nearby drivers.
- Routing and fare calculations happen on the backend.
- A ride dispatches sequentially, one offer at a time.
- Accept/reject/timeout logic is race-safe and idempotent.
- A driver cannot receive two active rides.
- Passenger and driver can complete the full ride lifecycle.
- Cancellation and `no_driver_found` are handled correctly.
- Ride history and event timeline work.
- RLS and Realtime protect each user's data/location.
- FastAPI is deployed over HTTPS and works with the production frontend.

---

## 25. Coordination checklist with the frontend developer

Agree these items before connecting each feature:

1. Exact endpoint method/path and request/response fields.
2. Role required for the action and which user may access the record.
3. All error codes the frontend must display.
4. GPS fields that may be null (`heading_deg`, `speed_mps`) and accepted update frequency.
5. Realtime table names, primary keys, filters and RLS access rules.
6. Whether the frontend uses map-click + manual labels for address selection (current V1 assumption) or a separately agreed geocoding provider.
7. Fare formula and rounding policy.
8. Driver verification process for the demo (manual admin operation in V1 unless an admin workflow is separately added).
9. Local frontend origin and final Vercel production origin for CORS.

Keep the API contract as the shared source of truth. If a request or response changes, update the contract and communicate the change to the frontend developer before merging.

---

## 26. Official references

Use the official documentation if package interfaces, Supabase connection modes, or hosting settings change.

- FastAPI first steps: https://fastapi.tiangolo.com/tutorial/first-steps/
- FastAPI SQL databases: https://fastapi.tiangolo.com/tutorial/sql-databases/
- FastAPI lifespan events: https://fastapi.tiangolo.com/advanced/events/
- Supabase JWT guide: https://supabase.com/docs/guides/auth/jwts
- Supabase Python `get_user(jwt)`: https://supabase.com/docs/reference/python/auth-getuser
- Supabase JWT signing keys: https://supabase.com/docs/guides/auth/signing-keys
- Supabase database connections and poolers: https://supabase.com/docs/guides/database/connecting-to-postgres
- Supabase PostGIS extension: https://supabase.com/docs/guides/database/extensions/postgis
- Supabase Row Level Security: https://supabase.com/docs/guides/database/postgres/row-level-security
- Supabase Realtime database changes: https://supabase.com/docs/guides/realtime/subscribing-to-database-changes
- Render FastAPI deployment: https://render.com/docs/deploy-fastapi
- Render Web Services: https://render.com/docs/web-services

---

**End of RideFlow V1 Backend Developer Guide**
