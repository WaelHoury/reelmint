"""FastAPI dependencies for the (single, local) user and session ownership.

Reelmint is a self-hosted tool without login. Every request acts as
one local user, created on first use. Ownership columns (``user_id``) are kept
so the data model stays unchanged.
"""

from fastapi import Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import User
from app.api.services.session_store import session_store, VideoSession

LOCAL_USER_EMAIL = "local@localhost"
LOCAL_USER_NAME = "Local User"
LOCAL_USER_EXTERNAL_ID = "local"  # stored in the (legacy) google_id column


def get_local_user(db: Session) -> User:
    """Return the single local user, creating it on first use."""
    user = db.query(User).filter(User.email == LOCAL_USER_EMAIL).first()
    if user:
        return user

    user = User(
        google_id=LOCAL_USER_EXTERNAL_ID,
        email=LOCAL_USER_EMAIL,
        name=LOCAL_USER_NAME,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Another request created it concurrently — use that row.
        db.rollback()
        user = db.query(User).filter(User.email == LOCAL_USER_EMAIL).first()
        if user is None:
            raise
        return user
    db.refresh(user)
    return user


def get_current_user(db: Session = Depends(get_db)) -> User:
    """Return the local user (no authentication in self-hosted mode)."""
    return get_local_user(db)


def get_current_user_from_token_param(db: Session = Depends(get_db)) -> User:
    """Return the local user. Kept for routes serving browser-native media URLs."""
    return get_local_user(db)


def get_current_user_from_header_or_token(db: Session = Depends(get_db)) -> User:
    """Return the local user. Kept for routes that previously accepted either auth form."""
    return get_local_user(db)


def get_optional_user(db: Session = Depends(get_db)) -> User | None:
    """Return the local user (always present in self-hosted mode)."""
    return get_local_user(db)


def get_owned_session(session_id: str, user: User) -> VideoSession:
    """Fetch a session and verify the current user owns it.

    Returns 404 for both missing and non-owned sessions to prevent
    session ID enumeration. Legacy sessions with user_id=None are
    inaccessible via the API (they expire from Redis naturally).
    """
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def is_superuser(user: User) -> bool:
    """Local single-user mode: no per-user generation limits."""
    return True
