from getpass import getpass
from datetime import datetime, timezone
import httpx

from app.core.config import settings

BASE = "http://127.0.0.1:8000/api/v1"
RIDE_ID = "2a94820d-6309-4d41-a65d-370b1a760cc4"

def login(email, password):
    response = httpx.post(
        f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": settings.supabase_publishable_key},
        json={"email": email, "password": password},
        timeout=20,
    )
    response.raise_for_status()
    return {
        "Authorization": f"Bearer {response.json()['access_token']}"
    }

def request(method, path, headers, expected=(200,)):
    response = httpx.request(
        method,
        f"{BASE}{path}",
        headers=headers,
        timeout=30,
    )
    print(f"\n{method} {path}: {response.status_code}")
    print(response.text[:2500])

    if response.status_code not in expected:
        raise RuntimeError(f"Unexpected response: {response.status_code}")

    return response

def unwrap(data):
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data

print("=== RIDEFLOW LIVE LIFECYCLE TEST ===")

passenger_email = input("\nPassenger email: ")
passenger_password = getpass("Passenger password: ")

driver_email = input("\nDriver email: ")
driver_password = getpass("Driver password: ")

passenger = login(passenger_email, passenger_password)
driver = login(driver_email, driver_password)

print("\nBoth accounts authenticated.")

# 1. Verify the current ride
response = request("GET", f"/rides/{RIDE_ID}", passenger)
ride = unwrap(response.json())

if ride.get("status") != "accepted":
    raise SystemExit(
        "Ride is not accepted. Current status: "
        + str(ride.get("status"))
    )

print("\nExisting ride is accepted. Proceeding.")

# 2. Driver arrives
request(
    "POST",
    f"/driver/rides/{RIDE_ID}/arrived",
    driver
)

# 3. Driver starts the trip
request(
    "POST",
    f"/driver/rides/{RIDE_ID}/start",
    driver
)

# 4. Refresh GPS while driver is busy
gps = httpx.post(
    f"{BASE}/drivers/me/location",
    headers=driver,
    json={
        "lat": 12.2965,
        "lng": 76.6405,
        "accuracy_m": 8,
        "heading_deg": 90,
        "speed_mps": 0,
        "device_recorded_at": datetime.now(timezone.utc).isoformat(),
    },
    timeout=20,
)
print("\nFresh GPS:", gps.status_code, gps.text)
gps.raise_for_status()

# 5. Driver completes the trip
request(
    "POST",
    f"/driver/rides/{RIDE_ID}/complete",
    driver
)

# 6. Verify completed ride
response = request("GET", f"/rides/{RIDE_ID}", passenger)
completed = unwrap(response.json())

if completed.get("status") != "completed":
    raise RuntimeError("Ride was not marked completed.")

print("\nFINAL FARE:", completed.get("final_fare"))

# 7. Passenger ride history
request(
    "GET",
    "/me/rides?role=passenger&limit=20",
    passenger
)

# 8. Driver ride history
request(
    "GET",
    "/me/rides?role=driver&limit=20",
    driver
)

# 9. Ride event timeline
request(
    "GET",
    f"/rides/{RIDE_ID}/events",
    passenger
)

print("\n====================================")
print("SUCCESS: LIVE RIDE LIFECYCLE TEST PASSED")
print("Ride ID:", RIDE_ID)
print("====================================")
