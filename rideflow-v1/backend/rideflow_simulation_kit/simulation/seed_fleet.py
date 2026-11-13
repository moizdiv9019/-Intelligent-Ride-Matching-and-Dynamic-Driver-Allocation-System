"""Create a small RideFlow demo fleet through Supabase Auth Admin + FastAPI.

Run from the backend directory:
    python simulation/seed_fleet.py

The Supabase secret/service-role key is prompted for and never written to disk.
The shared demo password is also prompted for and never written to disk.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

import httpx

def find_backend_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "app" / "main.py").exists():
            return parent
    raise SystemExit("Could not locate backend/app/main.py. Extract the kit inside backend/.")


BACKEND_DIR = find_backend_dir()
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from app.core.config import settings
except ImportError as exc:  # pragma: no cover - helpful CLI message
    raise SystemExit("Could not import backend settings. Check the backend virtual environment.") from exc


OUTPUT_ROOT = BACKEND_DIR / "simulation_data"
API_ORIGIN = "http://127.0.0.1:8000"
API_BASE = API_ORIGIN + getattr(settings, "api_prefix", "/api/v1")
SUPABASE_URL = (settings.supabase_url or "").rstrip("/")
PUBLISHABLE_KEY = settings.supabase_publishable_key or ""

DRIVER_SPECS = [
    {"number": 1, "name": "RideFlow Demo Driver 01", "vehicle_type": "car", "make_model": "Demo Sedan 01", "scenario": "verified_available_fresh", "lat": 12.2960, "lng": 76.6400},
    {"number": 2, "name": "RideFlow Demo Driver 02", "vehicle_type": "auto", "make_model": "Demo Auto 02", "scenario": "verified_available_fresh", "lat": 12.2980, "lng": 76.6420},
    {"number": 3, "name": "RideFlow Demo Driver 03", "vehicle_type": "bike", "make_model": "Demo Bike 03", "scenario": "verified_available_fresh", "lat": 12.2930, "lng": 76.6380},
    {"number": 4, "name": "RideFlow Demo Driver 04", "vehicle_type": "car", "make_model": "Demo Hatchback 04", "scenario": "verified_available_fresh", "lat": 12.3010, "lng": 76.6450},
    {"number": 5, "name": "RideFlow Demo Driver 05", "vehicle_type": "auto", "make_model": "Demo Auto 05", "scenario": "verified_available_stale_gps", "lat": 12.2975, "lng": 76.6425},
    {"number": 6, "name": "RideFlow Demo Driver 06", "vehicle_type": "bike", "make_model": "Demo Bike 06", "scenario": "verified_offline_fresh_gps", "lat": 12.2940, "lng": 76.6330},
    {"number": 7, "name": "RideFlow Demo Driver 07", "vehicle_type": "car", "make_model": "Demo Car 07", "scenario": "pending_verification", "lat": None, "lng": None},
]

PASSENGER_SPECS = [
    {"number": 1, "name": "RideFlow Demo Passenger 01"},
    {"number": 2, "name": "RideFlow Demo Passenger 02"},
]


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def create_auth_user(client: httpx.Client, secret_key: str, email: str, password: str, full_name: str) -> dict:
    response = client.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
        json={
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name, "name": full_name},
        },
    )
    if response.status_code not in (200, 201):
        # Avoid echoing request headers, passwords or keys in any error output.
        raise RuntimeError(f"Supabase Auth user creation failed for {email}: HTTP {response.status_code} {response.text[:500]}")
    body = response.json()
    user = body.get("user", body)
    if not user.get("id"):
        raise RuntimeError(f"Supabase did not return a user ID for {email}.")
    return {"id": user["id"], "email": user.get("email", email), "full_name": full_name}


def sign_in(client: httpx.Client, email: str, password: str) -> str:
    response = client.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": PUBLISHABLE_KEY},
        json={"email": email, "password": password},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not sign in newly created test account {email}: HTTP {response.status_code} {response.text[:300]}")
    return response.json()["access_token"]


def write_sql(run_dir: Path, drivers: list[dict]) -> None:
    verified_ids = [d["id"] for d in drivers if d["number"] <= 6]
    pending_id = next(d["id"] for d in drivers if d["number"] == 7)
    ids_sql = ",\n    ".join(f"'{value}'::uuid" for value in verified_ids)
    stale = next(d for d in drivers if d["number"] == 5)
    sql = f'''-- RideFlow simulation activation for run {run_dir.name}\n-- Run only after reviewing the IDs. This affects only the seven generated demo drivers.\nBEGIN;\n\nUPDATE public.driver_profiles\nSET verification_status = 'verified',\n    availability_status = 'offline',\n    updated_at = NOW()\nWHERE user_id IN (\n    {ids_sql}\n);\n\n-- Driver 05 is intentionally AVAILABLE with a stale GPS fix.\nUPDATE public.driver_profiles\nSET availability_status = 'available',\n    last_became_available_at = NOW(),\n    last_seen_at = NOW() - INTERVAL '10 minutes',\n    updated_at = NOW()\nWHERE user_id = '{stale['id']}'::uuid;\n\n-- Add/update one deliberately stale location (10 minutes old).\nINSERT INTO public.driver_locations (\n    driver_user_id, location, accuracy_m, heading_deg, speed_mps,\n    device_recorded_at, received_at\n) VALUES (\n    '{stale['id']}'::uuid,\n    ST_SetSRID(ST_MakePoint({stale['lng']}, {stale['lat']}), 4326)::geography,\n    20, 90, 0, NOW() - INTERVAL '10 minutes', NOW() - INTERVAL '10 minutes'\n)\nON CONFLICT (driver_user_id) DO UPDATE SET\n    location = EXCLUDED.location,\n    accuracy_m = EXCLUDED.accuracy_m,\n    heading_deg = EXCLUDED.heading_deg,\n    speed_mps = EXCLUDED.speed_mps,\n    device_recorded_at = EXCLUDED.device_recorded_at,\n    received_at = EXCLUDED.received_at;\n\n-- Driver 07 intentionally remains pending and offline.\nUPDATE public.driver_profiles\nSET verification_status = 'pending',\n    availability_status = 'offline',\n    updated_at = NOW()\nWHERE user_id = '{pending_id}'::uuid;\n\nCOMMIT;\n'''
    (run_dir / "activate_simulation.sql").write_text(sql, encoding="utf-8")

    all_ids = ",\n    ".join(f"'{d['id']}'::uuid" for d in drivers)
    inspect = f'''-- Inspect only this run's generated driver accounts.\nSELECT u.email, dp.user_id, dp.verification_status, dp.availability_status,\n       v.vehicle_type, v.is_active, dl.received_at,\n       ST_Y(dl.location::geometry) AS latitude,\n       ST_X(dl.location::geometry) AS longitude,\n       NOW() - dl.received_at AS location_age\nFROM auth.users u\nJOIN public.driver_profiles dp ON dp.user_id = u.id\nLEFT JOIN public.vehicles v ON v.driver_user_id = u.id AND v.is_active = TRUE\nLEFT JOIN public.driver_locations dl ON dl.driver_user_id = u.id\nWHERE u.id IN (\n    {all_ids}\n)\nORDER BY u.email;\n'''
    (run_dir / "inspect_simulation.sql").write_text(inspect, encoding="utf-8")

    idle_minutes = {1: 5, 2: 25, 3: 12, 4: 40}
    idle_statements = []
    for driver in drivers:
        if driver["number"] in idle_minutes:
            minutes = idle_minutes[driver["number"]]
            idle_statements.append(
                f"UPDATE public.driver_profiles SET last_became_available_at = NOW() - INTERVAL '{minutes} minutes', updated_at = NOW() WHERE user_id = '{driver['id']}'::uuid;"
            )
    idle_statements.append(
        "UPDATE public.driver_profiles dp SET last_became_available_at = NOW() - INTERVAL '30 minutes', updated_at = NOW() FROM auth.users u WHERE dp.user_id = u.id AND LOWER(u.email) = LOWER('zameer@gmail.com');"
    )
    idle_sql = (
        f"-- Apply varied synthetic idle durations after the API activation script.\n"
        f"-- Generated drivers and the existing Zameer demo account only.\n"
        "BEGIN;\n" + "\n".join(idle_statements) + "\nCOMMIT;\n"
    )
    (run_dir / "set_idle_times.sql").write_text(idle_sql, encoding="utf-8")


def main() -> int:
    if not (BACKEND_DIR / "app" / "main.py").exists():
        print("Could not locate backend/app/main.py. Run from the backend directory.")
        return 1
    if not SUPABASE_URL or not PUBLISHABLE_KEY:
        print("SUPABASE_URL or SUPABASE_PUBLISHABLE_KEY is missing in backend/.env.")
        return 1

    print("RideFlow simulation fleet generator")
    print("This will create 7 additional driver users and 2 additional passenger users.")
    print("Your existing Moiz passenger and Zameer driver accounts will be reused during activation.")
    confirm = input("Type CREATE-DEMO-DATA to continue: ").strip()
    if confirm != "CREATE-DEMO-DATA":
        print("Cancelled; no accounts were created.")
        return 0

    secret_key = getpass("Supabase project secret/service-role key (hidden): ").strip()
    password = getpass("Shared password for generated demo users (hidden): ")
    password_again = getpass("Confirm shared demo password (hidden): ")
    if not secret_key or len(password) < 8 or password != password_again:
        print("A secret key and matching password of at least 8 characters are required.")
        return 1

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:4]
    run_dir = OUTPUT_ROOT / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_url": SUPABASE_URL,
        "existing_accounts_reused": ["moiz9019455456@gmail.com", "zameer@gmail.com"],
        "generated_passengers": [],
        "generated_drivers": [],
        "notes": [
            "Passwords and Supabase secret keys are intentionally not stored in this manifest.",
            "Driver 01-04 are intended verified/available with fresh GPS.",
            "Driver 05 is verified/available with deliberately stale GPS.",
            "Driver 06 is verified/offline with fresh GPS.",
            "Driver 07 remains pending/offline.",
        ],
    }
    manifest_path = run_dir / "manifest.json"
    save_json(manifest_path, manifest)

    timeout = httpx.Timeout(30.0, connect=15.0)
    with httpx.Client(timeout=timeout) as client:
        print("\nCreating driver users and onboarding vehicles...")
        for spec in DRIVER_SPECS:
            email = f"rideflow.demo.driver{spec['number']:02d}.{run_id}@example.com"
            user = create_auth_user(client, secret_key, email, password, spec["name"])
            user.update({
                "number": spec["number"],
                "scenario": spec["scenario"],
                "vehicle_type": spec["vehicle_type"],
                "make_model": spec["make_model"],
                "lat": spec["lat"],
                "lng": spec["lng"],
            })
            manifest["generated_drivers"].append(user)
            save_json(manifest_path, manifest)

            token = sign_in(client, email, password)
            onboard = client.post(
                f"{API_BASE}/drivers/me/onboarding",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "vehicle_type": spec["vehicle_type"],
                    "make_model": spec["make_model"],
                    "registration_number": f"RF{run_id[-4:].upper()}D{spec['number']:02d}",
                    "seat_capacity": 2 if spec["vehicle_type"] == "bike" else (3 if spec["vehicle_type"] == "auto" else 4),
                    "wheelchair_accessible": False,
                },
            )
            if onboard.status_code not in (200, 201):
                save_json(manifest_path, manifest)
                raise RuntimeError(
                    f"Onboarding failed for {email}: HTTP {onboard.status_code} {onboard.text[:500]}. "
                    f"Partial manifest saved at {manifest_path}"
                )
            result = onboard.json()
            user["vehicle_id"] = result.get("vehicle", {}).get("id")
            user["onboarding_status"] = result.get("driver_profile", {}).get("verification_status")
            save_json(manifest_path, manifest)
            print(f"  Created {email} | driver {user['id']} | {spec['scenario']}")

        print("\nCreating two additional passenger users...")
        for spec in PASSENGER_SPECS:
            email = f"rideflow.demo.passenger{spec['number']:02d}.{run_id}@example.com"
            user = create_auth_user(client, secret_key, email, password, spec["name"])
            manifest["generated_passengers"].append(user)
            save_json(manifest_path, manifest)
            print(f"  Created {email} | passenger {user['id']}")

    write_sql(run_dir, manifest["generated_drivers"])
    manifest["activation_sql"] = str(run_dir / "activate_simulation.sql")
    manifest["inspection_sql"] = str(run_dir / "inspect_simulation.sql")
    manifest["idle_times_sql"] = str(run_dir / "set_idle_times.sql")
    save_json(manifest_path, manifest)

    print("\nFLEET CREATION COMPLETE")
    print(f"Manifest: {manifest_path}")
    print(f"Run this SQL manually in Supabase: {run_dir / 'activate_simulation.sql'}")
    print("Then run: python rideflow_simulation_kit/simulation/activate_and_locate.py")
    print("Do not share the secret key or the generated users' shared password.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Review the partial manifest in backend/simulation_data before retrying.")
        raise SystemExit(130)
