"""FastAPI dependencies for authentication and database access."""

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import User
from app.api.services.auth_service import decode_jwt

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Get the authenticated user from JWT token. Raises 401 if invalid."""
    payload = decode_jwt(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id = int(payload["sub"])
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


def get_current_user_from_token_param(
    token: str = Query(..., alias="token"),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate via ?token= query param. Used for browser-native requests
    (video <source>, download links) that can't send Authorization headers."""
    payload = decode_jwt(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id = int(payload["sub"])
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(optional_security),
    db: Session = Depends(get_db),
) -> User | None:
    """Get the authenticated user if a valid token is provided, otherwise None."""
    if credentials is None:
        return None

    payload = decode_jwt(credentials.credentials)
    if payload is None:
        return None

    user_id = int(payload["sub"])
    return db.query(User).filter(User.id == user_id).first()
