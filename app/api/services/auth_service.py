"""Google OAuth2 verification and JWT token management."""

from datetime import datetime, timedelta
from typing import Optional

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import User


def verify_google_token(token: str) -> dict:
    """Verify a Google ID token and return user info.

    Args:
        token: Google ID token from frontend

    Returns:
        Dict with google_id, email, name, picture

    Raises:
        ValueError: If token is invalid
    """
    try:
        idinfo = id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.google_client_id,
            clock_skew_in_seconds=30
        )
        return {
            "google_id": idinfo["sub"],
            "email": idinfo["email"],
            "name": idinfo.get("name", idinfo["email"].split("@")[0]),
            "picture": idinfo.get("picture"),
        }
    except Exception as e:
        raise ValueError(f"Invalid Google token: {e}")


def get_or_create_user(db: Session, google_info: dict) -> User:
    """Find existing user by Google ID or create a new one.

    Args:
        db: Database session
        google_info: Dict from verify_google_token

    Returns:
        User model instance
    """
    user = db.query(User).filter(User.google_id == google_info["google_id"]).first()

    if user:
        user.last_login_at = datetime.utcnow()
        user.name = google_info["name"]
        user.picture_url = google_info.get("picture")
        db.commit()
        db.refresh(user)
        return user

    user = User(
        google_id=google_info["google_id"],
        email=google_info["email"],
        name=google_info["name"],
        picture_url=google_info.get("picture"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_jwt(user_id: int, email: str) -> str:
    """Create a JWT access token.

    Args:
        user_id: Database user ID
        email: User email

    Returns:
        Encoded JWT string
    """
    expire = datetime.utcnow() + timedelta(hours=settings.jwt_expiry_hours)
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> Optional[dict]:
    """Decode and validate a JWT token.

    Args:
        token: JWT string

    Returns:
        Decoded payload dict or None if invalid
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None
