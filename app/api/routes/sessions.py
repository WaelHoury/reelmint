"""Session management API routes."""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    CreateSessionRequest,
    SessionResponse,
    SessionListResponse,
    SessionSettings,
)
from app.api.services.session_store import session_store
from app.api.deps import get_current_user
from app.db.base import get_db
from app.db.models import User, Video

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _normalize_title(title: str) -> str:
    """Normalize title for duplicate detection and storage."""
    return re.sub(r"\s+", " ", title.strip())


def _normalize_title_key(title: str) -> str:
    """Normalize title for case-insensitive comparisons."""
    return _normalize_title(title).casefold()


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: CreateSessionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new video generation session."""
    normalized_title = _normalize_title(request.title)
    normalized_title_key = _normalize_title_key(request.title)

    existing_videos = db.query(Video).filter(Video.user_id == user.id).all()
    for video in existing_videos:
        if _normalize_title_key(video.title) == normalized_title_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DUPLICATE_TITLE",
                    "field": "title",
                    "message": "A video with this title already exists. Choose a different title.",
                },
            )

    try:
        session = session_store.create(
            title=normalized_title,
            input_type=request.input_type,
            prompt=request.prompt,
            script_text=request.script,
            duration_seconds=request.duration_seconds,
            loop_mode=request.loop_mode,
            subtitles_enabled=request.subtitles_enabled,
            orientation=request.orientation,
            narration_style=request.narration_style,
            user_id=user.id,
        )
        return _session_to_response(session)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=SessionListResponse)
async def list_sessions(user: User = Depends(get_current_user)):
    """List all sessions."""
    sessions = session_store.list_all()
    return SessionListResponse(
        sessions=[_session_to_response(s) for s in sessions],
        total=len(sessions),
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, user: User = Depends(get_current_user)):
    """Get session by ID."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, user: User = Depends(get_current_user)):
    """Delete a session."""
    if not session_store.delete(session_id):
        raise HTTPException(status_code=404, detail="Session not found")


def _session_to_response(session) -> SessionResponse:
    """Convert session to response model."""
    return SessionResponse(
        session_id=session.session_id,
        status=session.status,
        title=session.title,
        created_at=session.created_at,
        input_type=session.input_type,
        settings=SessionSettings(
            duration_seconds=session.duration_seconds,
            loop_mode=session.loop_mode,
            subtitles_enabled=session.subtitles_enabled,
            orientation=session.orientation,
        ),
        script_text=session.script_text,
        word_count=session.word_count,
        estimated_duration_seconds=session.estimated_duration_seconds,
        error_message=session.error_message,
    )
