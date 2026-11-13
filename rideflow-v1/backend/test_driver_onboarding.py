from getpass import getpass
import httpx
from app.core.config import settings

email = input("Test driver email: ")
password = getpass("Test driver password: ")

login = httpx.post(
    f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
    params={"grant_type": "password"},
    headers={"apikey": settings.supabase_publishable_key},
    json={"email": email, "password": password},
    timeout=20,
)

if login.status_code != 200:
    print("Login failed:", login.status_code, login.text[:300])
    raise SystemExit(1)

token_data = login.json()
token = token_data["access_token"]
driver_id = token_data["user"]["id"]

result = httpx.post(
    "http://127.0.0.1:8000/api/v1/drivers/me/onboarding",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "vehicle_type": "car",
        "make_model": "Test Hatchback",
        "registration_number": "KA09ZZ9999",
        "seat_capacity": 4,
        "wheelchair_accessible": False
    },
    timeout=20
)

print("\nDriver UUID:", driver_id)
print("Onboarding status:", result.status_code)
print(result.text)
