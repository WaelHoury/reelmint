"""Clip generation and management API routes."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas import (
    ClipData,
    ClipsResponse,
    UpdateClipRequest,
)
from app.api.services.session_store import session_store
from app.api.services.video_service import video_service
from app.api.deps import get_current_user
from app.db.models import User

router = APIRouter(prefix="/sessions/{session_id}/clips", tags=["clips"])


@router.post("", response_model=ClipsResponse)
async def generate_clips(session_id: str, user: User = Depends(get_current_user)):
    """Generate visual clips for the confirmed script."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != "script_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Script must be confirmed first. Current status: {session.status}"
        )

    try:
        session = video_service.generate_clips(session)
        return _clips_response(session)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=ClipsResponse)
async def get_clips(session_id: str, user: User = Depends(get_current_user)):
    """Get current clips for session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.clips:
        raise HTTPException(status_code=404, detail="No clips generated yet")

    return _clips_response(session)


@router.put("/{clip_index}", response_model=ClipData)
async def update_clip(session_id: str, clip_index: int, request: UpdateClipRequest, user: User = Depends(get_current_user)):
    """Update a single clip's description and search for new video."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.clips:
        raise HTTPException(status_code=404, detail="No clips generated yet")

    if clip_index < 0 or clip_index >= len(session.clips):
        raise HTTPException(status_code=404, detail=f"Clip index {clip_index} not found")

    if session.status == "generating":
        raise HTTPException(status_code=400, detail="Cannot modify clips during generation")

    try:
        session = video_service.update_clip(
            session,
            clip_index,
            request.visual_description,
            request.keywords,
        )
        return _clip_to_data(session.clips[clip_index])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm", response_model=ClipsResponse)
async def confirm_clips(session_id: str, user: User = Depends(get_current_user)):
    """Confirm all clips are ready for video generation."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.clips:
        raise HTTPException(status_code=400, detail="No clips to confirm")

    try:
        session = video_service.confirm_clips(session)
        return _clips_response(session)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _clip_to_data(clip: dict) -> ClipData:
    """Convert clip dict to ClipData model."""
    return ClipData(
        index=clip.get('index', 0),
        keywords=clip.get('keywords', []),
        visual_description=clip.get('visual_description', ''),
        video_preview_url=clip.get('video_preview_url'),
        video_id=clip.get('video_id'),
        author=clip.get('author'),
        author_url=clip.get('author_url'),
        duration_seconds=clip.get('duration_seconds'),
    )


def _clips_response(session) -> ClipsResponse:
    """Build clips response from session."""
    return ClipsResponse(
        clips=[_clip_to_data(c) for c in session.clips],
        total_clips=len(session.clips),
        status=session.status,
    )
