import uuid
import time
from getpass import getpass
from datetime import datetime, timezone
import httpx

from app.core.config import settings

BASE = "http://127.0.0.1:8000/api/v1"
AUTH = settings.supabase_url.rstrip("/") + "/auth/v1/token"
API_KEY = settings.supabase_publishable_key

def login(email, password):
    r = httpx.post(
        AUTH,
        params={"grant_type": "password"},
        headers={"apikey": API_KEY},
        json={"email": email, "password": password},
        timeout=20
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}

def call(method, path, headers, body=None):
    r = httpx.request(
        method, BASE + path,
        headers=headers,
        json=body,
        timeout=30
    )
    print(f"\n{method} {path}: {r.status_code}")
    print(r.text[:1200])
    r.raise_for_status()
    return r.json()

def refresh_driver(driver):
    call("PATCH", "/drivers/me/availability", driver,
         {"availability_status": "available"})

    call("POST", "/drivers/me/location", driver, {
        "lat": 12.2958,
        "lng": 76.6394,
        "accuracy_m": 8,
        "heading_deg": 90,
        "speed_mps": 0,
        "device_recorded_at": datetime.now(timezone.utc).isoformat()
    })

def create_ride(passenger):
    body = {
        "client_request_id": str(uuid.uuid4()),
        "pickup": {"lat": 12.2965, "lng": 76.6405},
        "destination": {"lat": 12.3100, "lng": 76.6500},
        "pickup_address": "Test cancellation pickup",
        "destination_address": "Mysuru Palace"
    }
    return call("POST", "/rides", passenger, body)

def find_offer(value, ride_id):
    if isinstance(value, dict):
        if value.get("ride_id") == ride_id and (
            value.get("offer_id") or value.get("id")
        ):
            return value
        for child in value.values():
            result = find_offer(child, ride_id)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_offer(child, ride_id)
            if result:
                return result
    return None

passenger_email = input("Passenger email: ")
passenger_password = getpass("Passenger password: ")
driver_email = input("Driver email: ")
driver_password = getpass("Driver password: ")

passenger = login(passenger_email, passenger_password)
driver = login(driver_email, driver_password)

print("\nBoth accounts authenticated.")

# TEST A: Passenger cancellation
refresh_driver(driver)
ride_a = create_ride(passenger)
ride_a_id = ride_a["ride_id"]

cancel_a = call(
    "POST",
    f"/rides/{ride_a_id}/cancel",
    passenger,
    {"reason": "Live cancellation test"}
)

time.sleep(2)
status_a = call("GET", f"/rides/{ride_a_id}", passenger)
print("\nPASSENGER CANCELLATION STATUS:", status_a.get("status"))

# TEST B: Driver cancellation after accepting
refresh_driver(driver)
ride_b = create_ride(passenger)
ride_b_id = ride_b["ride_id"]

offer = None
for attempt in range(8):
    result = call(
        "GET",
        "/drivers/me/offers?status=offered",
        driver
    )
    offer = find_offer(result, ride_b_id)
    if offer:
        break
    time.sleep(2)

if not offer:
    raise SystemExit("No offer found for driver-cancellation test.")

offer_id = offer.get("offer_id") or offer.get("id")

call(
    "POST",
    f"/driver/offers/{offer_id}/accept",
    driver
)

call(
    "POST",
    f"/driver/rides/{ride_b_id}/cancel",
    driver,
    {"reason": "Live driver cancellation test"}
)

time.sleep(3)
status_b = call("GET", f"/rides/{ride_b_id}", passenger)

print("\nDRIVER CANCELLATION / REDISPATCH STATUS:",
      status_b.get("status"))

call("GET", f"/rides/{ride_b_id}/events", passenger)

print("\nCANCELLATION TESTS FINISHED.")
print("Passenger-cancelled ride:", ride_a_id)
print("Driver-cancelled ride:", ride_b_id)
