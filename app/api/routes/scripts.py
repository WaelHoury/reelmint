"""Script generation and management API routes."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas import (
    UpdateScriptRequest,
    ScriptResponse,
)
from app.api.services.session_store import session_store
from app.api.services.video_service import video_service
from app.api.deps import get_current_user
from app.db.models import User

router = APIRouter(prefix="/sessions/{session_id}/script", tags=["scripts"])


@router.post("", response_model=ScriptResponse)
async def generate_script(session_id: str, user: User = Depends(get_current_user)):
    """Generate or validate script for session.

    For prompt input: Generates narration via LLM
    For script input: Validates and calculates metrics
    """
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        session = video_service.generate_script(session)
        return ScriptResponse(
            script_text=session.script_text,
            word_count=session.word_count,
            estimated_duration_seconds=session.estimated_duration_seconds,
            status=session.status,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("", response_model=ScriptResponse)
async def update_script(session_id: str, request: UpdateScriptRequest, user: User = Depends(get_current_user)):
    """Update/edit the session script."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in ("draft", "script_ready"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit script in status: {session.status}"
        )

    try:
        session = video_service.update_script(session, request.script_text)
        return ScriptResponse(
            script_text=session.script_text,
            word_count=session.word_count,
            estimated_duration_seconds=session.estimated_duration_seconds,
            status=session.status,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=ScriptResponse)
async def get_script(session_id: str, user: User = Depends(get_current_user)):
    """Get current script for session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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
