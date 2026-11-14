"""Local user info route (no login — the app runs as a single local user)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db.models import User
from app.api.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    picture_url: str | None = None


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get the local user's info."""
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        picture_url=user.picture_url,
    )
