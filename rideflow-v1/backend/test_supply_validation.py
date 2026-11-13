from getpass import getpass
from datetime import datetime, timezone
import json
import httpx

from app.core.config import settings

BASE = "http://127.0.0.1:8000/api/v1"
AUTH = settings.supabase_url.rstrip("/") + "/auth/v1/token"

def login(email, password):
    r = httpx.post(
        AUTH,
        params={"grant_type": "password"},
        headers={"apikey": settings.supabase_publishable_key},
        json={"email": email, "password": password},
        timeout=20,
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}

# Authenticate both accounts first
driver_email = input("Driver email: ")
driver_password = getpass("Driver password: ")
admin_email = input("Admin email: ")
admin_password = getpass("Admin password: ")

driver = login(driver_email, driver_password)
admin = login(admin_email, admin_password)

print("\nBoth accounts authenticated.")

# Refresh driver availability
r = httpx.patch(
    f"{BASE}/drivers/me/availability",
    headers=driver,
    json={"availability_status": "available"},
    timeout=20,
)
print("\nAvailability:", r.status_code, r.text)
r.raise_for_status()

# Refresh GPS
r = httpx.post(
    f"{BASE}/drivers/me/location",
    headers=driver,
    json={
        "lat": 12.2958,
        "lng": 76.6394,
        "accuracy_m": 8,
        "heading_deg": 90,
        "speed_mps": 0,
        "device_recorded_at": datetime.now(timezone.utc).isoformat(),
    },
    timeout=20,
)
print("\nGPS:", r.status_code, r.text)
r.raise_for_status()

# Check actual zone supply
r = httpx.get(
    f"{BASE}/intelligence/zones/summary",
    headers=admin,
    params={"window_minutes": 60, "limit": 100},
    timeout=30,
)
print("\nZONE SUMMARY:", r.status_code)
print(json.dumps(r.json(), indent=2)[:4000])
r.raise_for_status()

# Check surge supply
r = httpx.get(
    f"{BASE}/intelligence/zones/surge",
    headers=admin,
    params={"hours_ahead": 1, "limit": 100},
    timeout=30,
)
print("\nSURGE:", r.status_code)
r.raise_for_status()

zones = r.json().get("zones", [])
driver_zone = "zone:1229:7663"

matching_zone = next(
    (z for z in zones if z["zone_id"] == driver_zone),
    None
)

print("\nDRIVER ZONE:", driver_zone)
print(json.dumps(matching_zone, indent=2))

# Check repositioning
r = httpx.get(
    f"{BASE}/intelligence/repositioning/recommendations",
    headers=admin,
    params={"hours_ahead": 1, "limit": 50},
    timeout=30,
)
print("\nREPOSITIONING:", r.status_code)
print(json.dumps(r.json(), indent=2)[:4000])
r.raise_for_status()

print("\nSUPPLY VALIDATION FINISHED.")
