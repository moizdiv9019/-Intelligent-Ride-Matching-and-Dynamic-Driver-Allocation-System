from getpass import getpass
from datetime import datetime, timezone
import httpx

from app.core.config import settings

BASE = "http://127.0.0.1:8000/api/v1"

if not settings.supabase_url:
    raise SystemExit("SUPABASE_URL is not configured. Set it in backend/.env.")

if not settings.supabase_publishable_key:
    raise SystemExit(
        "SUPABASE_PUBLISHABLE_KEY is not configured. Set it in backend/.env."
    )

supabase_url = settings.supabase_url.rstrip("/")
if not supabase_url.startswith(("http://", "https://")):
    raise SystemExit("SUPABASE_URL must start with http:// or https://.")

email = input("Driver email: ")
password = getpass("Driver password: ")

login = httpx.post(
    f"{supabase_url}/auth/v1/token",
    params={"grant_type": "password"},
    headers={"apikey": settings.supabase_publishable_key},
    json={"email": email, "password": password},
    timeout=20,
)

if login.status_code != 200:
    raise SystemExit(f"Login failed: {login.status_code}")

token = login.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Check driver profile
profile = httpx.get(f"{BASE}/drivers/me", headers=headers)
print("Driver profile:", profile.status_code, profile.text)

if profile.status_code != 200:
    raise SystemExit("Driver profile check failed")

# Set driver available
availability = httpx.patch(
    f"{BASE}/drivers/me/availability",
    headers=headers,
    json={"availability_status": "available"},
)
print("\nAvailability:", availability.status_code, availability.text)

if availability.status_code != 200:
    raise SystemExit("Availability update failed")

# Send a test GPS location
location = httpx.post(
    f"{BASE}/drivers/me/location",
    headers=headers,
    json={
        "lat": 12.2958,
        "lng": 76.6394,
        "accuracy_m": 10,
        "heading_deg": 90,
        "speed_mps": 0,
        "device_recorded_at": datetime.now(timezone.utc).isoformat()
    },
)
print("\nGPS update:", location.status_code, location.text)

if location.status_code != 200:
    raise SystemExit("GPS update failed")

print("\nSUCCESS: Driver is verified, available and has a GPS location.")