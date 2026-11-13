from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


UserRole = Literal["passenger", "driver", "admin"]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str
    phone_number: str | None = None
    avatar_url: str | None = None


class ProfileResponse(BaseModel):
    id: UUID
    full_name: str
    phone_number: str | None
    avatar_url: str | None
    account_status: Literal["active", "restricted", "suspended"]
    created_at: datetime
    updated_at: datetime


class CurrentUserResponse(BaseModel):
    id: UUID
    email: str | None
    profile: ProfileResponse | None
    roles: list[UserRole]
