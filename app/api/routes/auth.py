"""Authentication API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.api.services.auth_service import verify_google_token, get_or_create_user, create_jwt

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    id_token: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    picture_url: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    user: UserResponse


@router.post("/google", response_model=LoginResponse)
async def google_login(request: GoogleLoginRequest, db: Session = Depends(get_db)):
    """Authenticate with Google ID token, returns JWT."""
    try:
        google_info = verify_google_token(request.id_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    user = get_or_create_user(db, google_info)
    token = create_jwt(user.id, user.email)

    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            picture_url=user.picture_url,
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current authenticated user info."""
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        picture_url=user.picture_url,
    )
