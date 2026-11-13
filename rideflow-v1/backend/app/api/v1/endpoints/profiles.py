from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.profile import (
    CurrentUserResponse,
    ProfileResponse,
    ProfileUpdate,
)


router = APIRouter()


@router.get("/me", response_model=CurrentUserResponse)
def get_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserResponse:
    profile_row = db.execute(
        text(
            """
            SELECT id, full_name, phone_number, avatar_url, account_status,
                   created_at, updated_at
            FROM public.profiles
            WHERE id = :user_id
            """
        ),
        {"user_id": current_user.id},
    ).mappings().first()
    roles = db.execute(
        text(
            """
            SELECT role
            FROM public.user_roles
            WHERE user_id = :user_id
            ORDER BY role
            """
        ),
        {"user_id": current_user.id},
    ).scalars().all()

    profile = ProfileResponse.model_validate(dict(profile_row)) if profile_row else None
    return CurrentUserResponse(
        id=current_user.id,
        email=current_user.email,
        profile=profile,
        roles=roles,
    )


@router.put("/me/profile", response_model=ProfileResponse)
def update_my_profile(
    profile: ProfileUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    profile_row = db.execute(
        text(
            """
            INSERT INTO public.profiles (id, full_name, phone_number, avatar_url)
            VALUES (:user_id, :full_name, :phone_number, :avatar_url)
            ON CONFLICT (id) DO UPDATE
            SET full_name = EXCLUDED.full_name,
                phone_number = EXCLUDED.phone_number,
                avatar_url = EXCLUDED.avatar_url,
                updated_at = now()
            RETURNING id, full_name, phone_number, avatar_url, account_status,
                      created_at, updated_at
            """
        ),
        {
            "user_id": current_user.id,
            "full_name": profile.full_name,
            "phone_number": profile.phone_number,
            "avatar_url": profile.avatar_url,
        },
    ).mappings().one()
    db.commit()

    return ProfileResponse.model_validate(dict(profile_row))
