from getpass import getpass
import httpx
from app.core.config import settings

if not settings.supabase_url:
    raise SystemExit("SUPABASE_URL is not configured. Set it in backend/.env.")

if not settings.supabase_publishable_key:
    raise SystemExit(
        "SUPABASE_PUBLISHABLE_KEY is not configured. Set it in backend/.env."
    )

email = input("Passenger email: ")
password = getpass("Passenger password: ")

response = httpx.post(
    f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
    params={"grant_type": "password"},
    headers={"apikey": settings.supabase_publishable_key},
    json={"email": email, "password": password},
    timeout=20,
)

if response.status_code != 200:
    print("Supabase login failed:", response.status_code)
    print(response.text[:400])
    raise SystemExit(1)

token = response.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
base = "http://127.0.0.1:8000/api/v1"

print("\nSupabase authentication: SUCCESS")

# Test profile endpoint
me = httpx.get(f"{base}/me", headers=headers, timeout=20)
print("\nGET /me:", me.status_code)
print(me.text)

if me.status_code != 200:
    raise SystemExit("Profile API failed")

# Test actual OSRM integration through FastAPI
estimate = httpx.post(
    f"{base}/rides/estimate",
    headers=headers,
    json={
        "pickup": {"lat": 12.2958, "lng": 76.6394},
        "destination": {"lat": 12.3100, "lng": 76.6500}
    },
    timeout=30,
)

print("\nPOST /rides/estimate:", estimate.status_code)
print(estimate.text)

if estimate.status_code == 200:
    print("\nSUCCESS: Authentication + database + routing + fare integration passed!")
