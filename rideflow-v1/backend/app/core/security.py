from typing import Any
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings


_bearer_scheme = HTTPBearer(auto_error=False)
_SUPABASE_TIMEOUT_SECONDS = 10.0


class AuthenticatedUser(BaseModel):
    id: UUID
    email: str | None = None
    user_metadata: dict[str, Any] = Field(default_factory=dict)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Supabase authentication service is unavailable.",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    supabase_url = settings.supabase_url
    publishable_key = settings.supabase_publishable_key
    if not supabase_url or not supabase_url.strip() or not publishable_key or not publishable_key.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase authentication is not configured.",
        )

    try:
        async with httpx.AsyncClient(timeout=_SUPABASE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{supabase_url.rstrip('/')}/auth/v1/user",
                headers={
                    "apikey": publishable_key,
                    "Authorization": f"Bearer {credentials.credentials}",
                },
            )
    except httpx.TimeoutException as exc:
        raise _service_unavailable() from exc
    except httpx.RequestError as exc:
        raise _service_unavailable() from exc

    if response.status_code in (400, 401, 403):
        raise _unauthorized()
    if not response.is_success:
        raise _service_unavailable()

    try:
        return AuthenticatedUser.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise _service_unavailable() from exc
