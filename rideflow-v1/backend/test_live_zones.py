from getpass import getpass
import httpx
from app.core.config import settings

email = input("Admin test email: ")
password = getpass("Password: ")

login = httpx.post(
    f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
    params={"grant_type": "password"},
    headers={"apikey": settings.supabase_publishable_key},
    json={"email": email, "password": password},
    timeout=20
)
login.raise_for_status()

token = login.json()["access_token"]

response = httpx.get(
    "http://127.0.0.1:8080/api/v1/intelligence/zones/summary",
    headers={"Authorization": f"Bearer {token}"},
    params={"window_minutes": 60, "limit": 100},
    timeout=30
)

print("Zone API status:", response.status_code)
print(response.text)

if response.status_code == 200:
    print("\nSUCCESS: Live Zone Intelligence API working!")
