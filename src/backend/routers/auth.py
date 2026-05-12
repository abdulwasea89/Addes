"""Auth router — ``GET /api/auth/me``.

The backend does not implement login, signup, or refresh — those go directly
to Supabase Auth from the frontend. This router exposes a single endpoint
that returns the verified caller's identity, so the frontend can sanity-check
that its JWT works against the backend.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.auth import CurrentUser, get_current_user
from backend.schemas import UserMe

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=UserMe)
async def get_me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> UserMe:
    full_name = user.claims.get("user_metadata", {}).get("full_name")
    return UserMe(
        user_id=user.id,
        email=user.email,
        full_name=full_name if isinstance(full_name, str) else None,
        role=user.role,
    )
