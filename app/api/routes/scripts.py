"""Script generation and management API routes."""

import asyncio
import functools
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)

from app.api.schemas import (
    UpdateScriptRequest,
    ScriptResponse,
)
from app.api.services.session_store import session_store
from app.api.services.video_service import video_service
from app.api.deps import get_current_user, get_owned_session
from app.db.models import User
from app.api.limiter import limiter

router = APIRouter(prefix="/sessions/{session_id}/script", tags=["scripts"])


@router.post("", response_model=ScriptResponse)
@limiter.limit("10/minute")
async def generate_script(request: Request, session_id: str, user: User = Depends(get_current_user)):
    """Generate or validate script for session.

    For prompt input: Generates narration via LLM
    For script input: Validates and calculates metrics
    """
    session = get_owned_session(session_id, user)

    try:
        loop = asyncio.get_event_loop()
        session = await loop.run_in_executor(None, video_service.generate_script, session)
        return ScriptResponse(
            script_text=session.script_text,
            word_count=session.word_count,
            estimated_duration_seconds=session.estimated_duration_seconds,
            status=session.status,
        )
    except Exception as e:
        logger.exception("Script generation failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Script generation failed")


@router.put("", response_model=ScriptResponse)
async def update_script(session_id: str, request: UpdateScriptRequest, user: User = Depends(get_current_user)):
    """Update/edit the session script."""
    session = get_owned_session(session_id, user)

    if session.status not in ("draft", "script_ready"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit script in status: {session.status}"
        )

    try:
        loop = asyncio.get_event_loop()
        session = await loop.run_in_executor(
            None, functools.partial(video_service.update_script, session, request.script_text)
        )
        return ScriptResponse(
            script_text=session.script_text,
            word_count=session.word_count,
            estimated_duration_seconds=session.estimated_duration_seconds,
            status=session.status,
        )
    except Exception as e:
        logger.exception("Script update failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to update script")


@router.get("", response_model=ScriptResponse)
async def get_script(session_id: str, user: User = Depends(get_current_user)):
    """Get current script for session."""
    session = get_owned_session(session_id, user)

    if not session.script_text:
        raise HTTPException(status_code=404, detail="No script generated yet")

    return ScriptResponse(
        script_text=session.script_text,
        word_count=session.word_count,
        estimated_duration_seconds=session.estimated_duration_seconds,
        status=session.status,
    )


@router.post("/confirm", response_model=ScriptResponse)
async def confirm_script(session_id: str, user: User = Depends(get_current_user)):
    """Confirm script is ready, advance to clips stage."""
    session = get_owned_session(session_id, user)

    if not session.script_text:
        raise HTTPException(status_code=400, detail="No script to confirm")

    try:
        session = video_service.confirm_script(session)
        return ScriptResponse(
            script_text=session.script_text,
            word_count=session.word_count,
            estimated_duration_seconds=session.estimated_duration_seconds,
            status=session.status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
