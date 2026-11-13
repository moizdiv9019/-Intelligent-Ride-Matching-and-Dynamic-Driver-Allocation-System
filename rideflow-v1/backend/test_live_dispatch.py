
import time
import uuid
from getpass import getpass
from datetime import datetime, timezone

import httpx
from app.core.config import settings

BASE = "http://127.0.0.1:8000/api/v1"
AUTH = settings.supabase_url.rstrip("/") + "/auth/v1/token"
API_KEY = settings.supabase_publishable_key

def login(email, password):
    response = httpx.post(
        AUTH,
        params={"grant_type": "password"},
        headers={"apikey": API_KEY},
        json={"email": email, "password": password},
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Login failed for {email}: {response.status_code} "
            f"{response.text[:300]}"
        )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

def get_offers(value):
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            if any("offer_id" in item or "id" in item for item in value):
                return value
        for item in value:
            found = get_offers(item)
            if found:
                return found
    elif isinstance(value, dict):
        for key in ("offers", "data", "items"):
            if key in value:
                found = get_offers(value[key])
                if found:
                    return found
        for item in value.values():
            found = get_offers(item)
            if found:
                return found
    return []

# Authenticate both test accounts
passenger_email = input("Passenger email: ")
passenger_password = getpass("Passenger password: ")
passenger_headers = login(passenger_email, passenger_password)

driver_email = input("Driver email: ")
driver_password = getpass("Driver password: ")
driver_headers = login(driver_email, driver_password)

print("\nBoth accounts authenticated.")

# 1. Refresh driver availability
availability = httpx.patch(
    f"{BASE}/drivers/me/availability",
    headers=driver_headers,
    json={"availability_status": "available"},
    timeout=20,
)
print("\nDriver availability:", availability.status_code)
print(availability.text)
availability.raise_for_status()

# 2. Refresh GPS immediately before booking
gps = httpx.post(
    f"{BASE}/drivers/me/location",
    headers=driver_headers,
    json={
        "lat": 12.2958,
        "lng": 76.6394,
        "accuracy_m": 10,
        "heading_deg": 90,
        "speed_mps": 0,
        "device_recorded_at": datetime.now(timezone.utc).isoformat(),
    },
    timeout=20,
)
print("\nDriver GPS:", gps.status_code)
print(gps.text)
gps.raise_for_status()

# 3. Passenger creates a real ride
booking_request = {
    "client_request_id": str(uuid.uuid4()),
    "pickup": {"lat": 12.2965, "lng": 76.6405},
    "destination": {"lat": 12.3100, "lng": 76.6500},
    "pickup_address": "Test pickup near Mysuru Railway Station",
    "destination_address": "Mysuru Palace",
}

booking = httpx.post(
    f"{BASE}/rides",
    headers=passenger_headers,
    json=booking_request,
    timeout=45,
)

print("\nRIDE BOOKING:", booking.status_code)
print(booking.text)

if booking.status_code not in (200, 201):
    raise SystemExit("Ride booking failed. Do not retry blindly.")

print("\nRide created. Checking driver offers...")

# 4. Check driver's incoming offer
offers_response = None
offers = []

for attempt in range(6):
    offers_response = httpx.get(
        f"{BASE}/drivers/me/offers",
        headers=driver_headers,
        params={"status": "offered"},
        timeout=20,
    )
    if offers_response.status_code != 200:
        print(offers_response.status_code, offers_response.text)
        raise SystemExit("Could not retrieve driver offers.")

    offers = get_offers(offers_response.json())
    if offers:
        break
    time.sleep(2)

if not offers:
    print("No active offer was returned.")
    print("Offer API response:", offers_response.text)
    raise SystemExit("Inspect dispatch and offer-expiry logs before retrying.")

offer = offers[0]
offer_id = offer.get("offer_id") or offer.get("id")

if not offer_id:
    print("Offer response:", offer)
    raise SystemExit("Could not identify the offer ID.")

print("\nDRIVER RECEIVED OFFER:")
print(offer)

# 5. Driver accepts the offer
acceptance = httpx.post(
    f"{BASE}/driver/offers/{offer_id}/accept",
    headers=driver_headers,
    timeout=20,
)

print("\nOFFER ACCEPTANCE:", acceptance.status_code)
print(acceptance.text)

if acceptance.status_code not in (200, 201):
    raise SystemExit("Offer acceptance failed.")

print("\nSUCCESS: LIVE RIDE DISPATCH AND DRIVER ACCEPTANCE COMPLETED.")

'@ | Set-Content .\test_live_dispatch.py -Encoding utf8'