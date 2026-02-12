"""Clip generation and management API routes."""

import asyncio
import functools
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from app.api.schemas import (
    ClipData,
    ClipsResponse,
    UpdateClipRequest,
)
from app.api.services.session_store import session_store
from app.api.services.video_service import video_service
from app.api.deps import get_current_user, get_owned_session
from app.db.models import User
from app.api.limiter import limiter

router = APIRouter(prefix="/sessions/{session_id}/clips", tags=["clips"])


@router.post("", response_model=ClipsResponse)
@limiter.limit("10/minute")
async def generate_clips(request: Request, session_id: str, user: User = Depends(get_current_user)):
    """Start clip generation (returns immediately).

    Returns 202 when starting a new generation, 200 if already in progress or done.
    """
    session = get_owned_session(session_id, user)

    if session.status not in ("script_ready", "clips_generating", "clips_ready"):
        raise HTTPException(
            status_code=400,
            detail=f"Script must be confirmed first. Current status: {session.status}"
        )

    # Already done
    if session.status == "clips_ready" and session.clips:
        return _clips_response(session)

    try:
        loop = asyncio.get_event_loop()
        session = await loop.run_in_executor(None, video_service.generate_clips, session)

        response = _clips_response(session)
        # 202 if generation just started, 200 if already in progress
        status_code = 202 if session.status == "clips_generating" else 200
        return JSONResponse(content=response.model_dump(), status_code=status_code)
    except Exception as e:
        logger.exception("Clip generation failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Clip generation failed")


@router.get("", response_model=ClipsResponse)
async def get_clips(session_id: str, user: User = Depends(get_current_user)):
    """Get current clips for session (returns partial data during generation)."""
    session = get_owned_session(session_id, user)

    if session.status not in ("clips_generating", "clips_ready") and not session.clips:
        raise HTTPException(status_code=404, detail="No clips generated yet")

    return _clips_response(session)


@router.put("/{clip_index}", response_model=ClipData)
async def update_clip(session_id: str, clip_index: int, request: UpdateClipRequest, user: User = Depends(get_current_user)):
    """Update a single clip's description and search for new video."""
    session = get_owned_session(session_id, user)

    if not session.clips:
        raise HTTPException(status_code=404, detail="No clips generated yet")

    if clip_index < 0 or clip_index >= len(session.clips):
        raise HTTPException(status_code=404, detail=f"Clip index {clip_index} not found")

    if session.status == "generating":
        raise HTTPException(status_code=400, detail="Cannot modify clips during generation")

    try:
        loop = asyncio.get_event_loop()
        session = await loop.run_in_executor(
            None,
            functools.partial(
                video_service.update_clip, session, clip_index,
                request.visual_description, request.keywords,
            ),
        )
        return _clip_to_data(session.clips[clip_index])
    except Exception as e:
        logger.exception("Clip update failed for session %s clip %d", session_id, clip_index)
        raise HTTPException(status_code=500, detail="Failed to update clip")


@router.post("/confirm", response_model=ClipsResponse)
async def confirm_clips(session_id: str, user: User = Depends(get_current_user)):
    """Confirm all clips are ready for video generation."""
    session = get_owned_session(session_id, user)

    if not session.clips:
        raise HTTPException(status_code=400, detail="No clips to confirm")

    if session.status == "clips_generating":
        raise HTTPException(status_code=400, detail="Clips are still generating. Please wait.")

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
        search_status=clip.get('search_status', 'pending'),
        search_error=clip.get('search_error'),
    )


def _clips_response(session) -> ClipsResponse:
    """Build clips response from session."""
    clips = [_clip_to_data(c) for c in session.clips]
    total = len(clips)
    completed = sum(1 for c in clips if c.search_status in ('ready', 'failed'))
    progress = int((completed / total) * 100) if total > 0 else 0

    return ClipsResponse(
        clips=clips,
        total_clips=total,
        status=session.status,
        completed_clips=completed,
        progress_percent=progress,
    )
