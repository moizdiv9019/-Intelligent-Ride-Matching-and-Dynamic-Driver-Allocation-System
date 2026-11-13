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

# Collect credentials before refreshing GPS
driver_email = input("Driver email: ")
driver_password = getpass("Driver password: ")
admin_email = input("Admin email: ")
admin_password = getpass("Admin password: ")

driver = login(driver_email, driver_password)
admin = login(admin_email, admin_password)

# Refresh GPS immediately before analysis
r = httpx.patch(
    f"{BASE}/drivers/me/availability",
    headers=driver,
    json={"availability_status": "available"},
    timeout=20,
)
print("Availability:", r.status_code, r.text)
r.raise_for_status()

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
print("GPS:", r.status_code, r.text)
r.raise_for_status()

# Test both intelligence endpoints
for name, path in [
    ("SURGE", "/intelligence/zones/surge"),
    ("REPOSITIONING", "/intelligence/repositioning/recommendations"),
]:
    r = httpx.get(
        f"{BASE}{path}",
        headers=admin,
        params={"hours_ahead": 1, "limit": 100},
        timeout=60,
    )
    print(f"\n{name}: HTTP {r.status_code}")
    print(json.dumps(r.json(), indent=2)[:5000])
    r.raise_for_status()

print("\nSUCCESS: Fresh driver and intelligence checks completed.")
