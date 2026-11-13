from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.profile import UserRole


def require_roles(*allowed_roles: UserRole) -> Callable[..., AuthenticatedUser]:
    if not allowed_roles:
        raise ValueError("At least one role must be specified.")

    def role_dependency(
        current_user: AuthenticatedUser = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AuthenticatedUser:
        roles = db.execute(
            text("SELECT role FROM public.user_roles WHERE user_id = :user_id"),
            {"user_id": current_user.id},
        ).scalars().all()

        if not set(roles).intersection(allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role.",
            )

        return current_user

    return role_dependency
