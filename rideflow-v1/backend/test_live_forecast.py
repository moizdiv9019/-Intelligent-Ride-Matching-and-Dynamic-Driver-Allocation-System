from getpass import getpass
import json
import httpx

from app.core.config import settings

email = input("Admin email: ")
password = getpass("Admin password: ")

login = httpx.post(
    f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
    params={"grant_type": "password"},
    headers={"apikey": settings.supabase_publishable_key},
    json={"email": email, "password": password},
    timeout=30,
)

if login.status_code != 200:
    raise SystemExit(f"Login failed: {login.status_code}")

token = login.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

base = "http://127.0.0.1:8000/api/v1/intelligence/zones"

# Test all supported forecast horizons
for hours in (1, 3, 6):
    response = httpx.get(
        f"{base}/forecast",
        headers=headers,
        params={"hours_ahead": hours, "limit": 100},
        timeout=60,
    )

    print(f"\nFORECAST {hours} HOURS: HTTP {response.status_code}")
    print(json.dumps(response.json(), indent=2)[:2500])

    if response.status_code != 200:
        raise SystemExit("Forecast endpoint failed.")

# Test model evaluation
evaluation = httpx.get(
    f"{base}/forecast/evaluation",
    headers=headers,
    timeout=60,
)

print("\nFORECAST EVALUATION:", evaluation.status_code)
print(json.dumps(evaluation.json(), indent=2)[:3500])

if evaluation.status_code != 200:
    raise SystemExit("Evaluation endpoint failed.")

print("\nSUCCESS: Live forecasting endpoints passed.")
