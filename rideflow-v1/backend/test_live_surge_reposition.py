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
    timeout=20,
)
login.raise_for_status()

headers = {
    "Authorization": f"Bearer {login.json()['access_token']}"
}

base = "http://127.0.0.1:8000/api/v1/intelligence"

tests = [
    (
        "SURGE PRICING",
        f"{base}/zones/surge",
        {"hours_ahead": 1, "limit": 100},
    ),
    (
        "DRIVER REPOSITIONING",
        f"{base}/repositioning/recommendations",
        {"hours_ahead": 1, "limit": 50},
    ),
]

for name, url, params in tests:
    print(f"\n===== {name} =====")

    try:
        response = httpx.get(
            url,
            headers=headers,
            params=params,
            timeout=60,
        )

        print("HTTP:", response.status_code)
        print(json.dumps(response.json(), indent=2)[:5000])

        if response.status_code != 200:
            print("Endpoint needs investigation.")

    except httpx.RequestError as error:
        print("Connection error:", str(error))

print("\nLIVE INTELLIGENCE TEST FINISHED")
