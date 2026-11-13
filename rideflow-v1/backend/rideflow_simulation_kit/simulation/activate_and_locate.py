"""Activate simulated drivers and seed GPS through the existing FastAPI APIs.

Run from the backend directory after executing the generated activate_simulation.sql:
    python simulation/activate_and_locate.py
"""
from __future__ import annotations

import json
import sys
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
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Could not import backend settings. Check the backend virtual environment.") from exc

API_BASE = "http://127.0.0.1:8000" + getattr(settings, "api_prefix", "/api/v1")
SUPABASE_URL = (settings.supabase_url or "").rstrip("/")
PUBLISHABLE_KEY = settings.supabase_publishable_key or ""

EXISTING_ZAMEER_LOCATION = {"lat": 12.2958, "lng": 76.6394}
DRIVER_LOCATIONS = {
    1: {"lat": 12.2960, "lng": 76.6400},
    2: {"lat": 12.2980, "lng": 76.6420},
    3: {"lat": 12.2930, "lng": 76.6380},
    4: {"lat": 12.3010, "lng": 76.6450},
    6: {"lat": 12.2940, "lng": 76.6330},
}


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": PUBLISHABLE_KEY},
        json={"email": email, "password": password},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Login failed for {email}: HTTP {response.status_code} {response.text[:300]}")
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def api(client: httpx.Client, method: str, path: str, headers: dict, body: dict | None = None) -> dict:
    response = client.request(method, f"{API_BASE}{path}", headers=headers, json=body)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"{method} {path} failed: HTTP {response.status_code} {response.text[:500]}")
    return response.json() if response.content else {}


def send_location(client: httpx.Client, headers: dict, point: dict) -> None:
    api(client, "POST", "/drivers/me/location", headers, {
        "lat": point["lat"],
        "lng": point["lng"],
        "accuracy_m": 8,
        "heading_deg": 90,
        "speed_mps": 0,
        "device_recorded_at": datetime.now(timezone.utc).isoformat(),
    })


def main() -> int:
    if not SUPABASE_URL or not PUBLISHABLE_KEY:
        print("Supabase URL or publishable key is missing from backend/.env")
        return 1

    manifest_input = input("Manifest path (from backend, e.g. simulation_data/run_.../manifest.json): ").strip().strip('"')
    manifest_path = Path(manifest_input)
    if not manifest_path.is_absolute():
        manifest_path = BACKEND_DIR / manifest_path
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    password = getpass("Shared password used when creating generated demo users (hidden): ")
    zameer_email = input("Existing driver email [zameer@gmail.com]: ").strip() or "zameer@gmail.com"
    zameer_password = getpass("Existing driver password (hidden): ")

    with httpx.Client(timeout=httpx.Timeout(25.0, connect=10.0)) as client:
        print("\nRefreshing existing Zameer test driver...")
        zameer = login(client, zameer_email, zameer_password)
        api(client, "PATCH", "/drivers/me/availability", zameer, {"availability_status": "available"})
        send_location(client, zameer, EXISTING_ZAMEER_LOCATION)
        print("  Zameer: verified/available/fresh GPS")

        for driver in manifest.get("generated_drivers", []):
            number = driver["number"]
            email = driver["email"]
            if number == 7:
                print(f"  {email}: left pending/offline (verification filter scenario)")
                continue

            headers = login(client, email, password)
            if number in (1, 2, 3, 4):
                api(client, "PATCH", "/drivers/me/availability", headers, {"availability_status": "available"})
                send_location(client, headers, DRIVER_LOCATIONS[number])
                print(f"  {email}: verified/available/fresh GPS at {DRIVER_LOCATIONS[number]}")
            elif number == 5:
                # SQL activation inserts a deliberately 10-minute-old GPS row.
                api(client, "PATCH", "/drivers/me/availability", headers, {"availability_status": "available"})
                print(f"  {email}: verified/available/stale GPS (should be filtered out)")
            elif number == 6:
                # To create a real fresh location, first go online, post GPS, then go offline.
                api(client, "PATCH", "/drivers/me/availability", headers, {"availability_status": "available"})
                send_location(client, headers, DRIVER_LOCATIONS[number])
                api(client, "PATCH", "/drivers/me/availability", headers, {"availability_status": "offline"})
                print(f"  {email}: verified/offline/fresh GPS (should be filtered out)")

    print("\nSIMULATION LOCATION SETUP COMPLETE")
    print("Eligible candidates should include Zameer and generated drivers 01-04.")
    print("Driver 05: stale GPS; driver 06: offline; driver 07: pending verification.")
    print("Keep this script and the manifest local; do not commit simulation_data.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
