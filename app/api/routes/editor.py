"""Video editor API routes — load editor data, render changes, search clips."""

import asyncio
import functools
import json
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.db.base import get_db
from app.db.models import User, Video
from app.api.deps import get_current_user, get_current_user_from_token_param, get_owned_session
from app.api.services.session_store import session_store
from app.api.services.editor_service import editor_service
from app.utils.helpers import stream_file_response, project_lock
from app.api.limiter import limiter
from app.api.schemas.editor import (
    EditorDataResponse,
    EditorRenderRequest,
    EditorRenderResponse,
    ClipSearchRequest,
    ClipSearchResult,
)
from typing import List

router = APIRouter(tags=["editor"])


# ─── Session-based editor endpoints ───


@router.get("/sessions/{session_id}/editor-data", response_model=EditorDataResponse)
async def get_session_editor_data(
    session_id: str,
    user: User = Depends(get_current_user),
):
    """Load editor data for a session. Requires project files to be persisted."""
    session = get_owned_session(session_id, user)
    if session.status != "completed":
        raise HTTPException(status_code=400, detail="Video must be completed before editing")

    # Find project directory
    project_dir = _resolve_project_dir_from_session(session)
    if not project_dir:
        raise HTTPException(
            status_code=400,
            detail="Project data not available. The video may have been generated before the editor feature was added.",
        )

    project = editor_service.load_project(project_dir)

    # Generate subtitle-free preview for the editor
    editor_service.generate_preview(project_dir, project)

    stream_url = f"/api/sessions/{session_id}/video/stream"
    preview_stream_url = f"/api/sessions/{session_id}/editor/preview-stream"

    return editor_service.get_editor_data(
        project_dir, project, stream_url,
        session_id=session_id,
        video_id=project.get("video_id"),
        preview_stream_url=preview_stream_url,
    )


@router.put("/sessions/{session_id}/editor/render", response_model=EditorRenderResponse)
@limiter.limit("5/minute")
async def render_session_editor(
    request: Request,
    session_id: str,
    render_request: EditorRenderRequest,
    user: User = Depends(get_current_user),
):
    """Render editor changes for a session."""
    session = get_owned_session(session_id, user)

    project_dir = _resolve_project_dir_from_session(session)
    if not project_dir:
        raise HTTPException(status_code=400, detail="Project data not available for this video")

    project = editor_service.load_project(project_dir)
    output_path = Path(session.output_path) if session.output_path else None
    if not output_path:
        raise HTTPException(status_code=400, detail="No output path for this session")

    # Use project dir name as lock key to prevent concurrent file mutations
    lock_id = project_dir.name

    def _render_with_lock():
        with project_lock(lock_id):
            return editor_service.render_changes(
                project_dir, project,
                subtitles=render_request.subtitles.model_dump() if render_request.subtitles else None,
                audio=render_request.audio.model_dump() if render_request.audio else None,
                replaced_clips=[c.model_dump() for c in render_request.replaced_clips] if render_request.replaced_clips else None,
                output_path=output_path,
            )

    try:
        loop = asyncio.get_event_loop()
        render_type = await loop.run_in_executor(None, _render_with_lock)
    except HTTPException:
        raise  # Re-raise 409 from lock contention
    except Exception as e:
        logger.exception("Video render failed")
        raise HTTPException(status_code=500, detail="Render failed")

    return EditorRenderResponse(
        status="ok",
        render_type=render_type,
        message=f"Video re-rendered successfully ({render_type})",
    )


@router.get("/sessions/{session_id}/editor/preview-stream")
async def stream_session_preview(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user_from_token_param),
):
    """Stream the subtitle-free preview video for editor playback."""
    session = get_owned_session(session_id, user)

    project_dir = _resolve_project_dir_from_session(session)
    if not project_dir:
        raise HTTPException(status_code=400, detail="Project data not available")

    project = editor_service.load_project(project_dir)
    preview_path = editor_service.generate_preview(project_dir, project)

    if not preview_path or not preview_path.exists():
        raise HTTPException(status_code=404, detail="Preview not available")

    return stream_file_response(preview_path, request)


# ─── Library video editor endpoints ───


@router.get("/videos/{video_id}/editor-data", response_model=EditorDataResponse)
async def get_library_editor_data(
    video_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Load editor data for a library video."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    project_dir = _resolve_project_dir_from_video(video)
    if not project_dir:
        raise HTTPException(
            status_code=400,
            detail="Project data not available. This video was generated before the editor feature was added.",
        )

    project = editor_service.load_project(project_dir)

    # Generate subtitle-free preview for the editor
    editor_service.generate_preview(project_dir, project)

    stream_url = f"/api/videos/{video_id}/stream"
    preview_stream_url = f"/api/videos/{video_id}/editor/preview-stream"

    return editor_service.get_editor_data(
        project_dir, project, stream_url,
        video_id=video_id,
        preview_stream_url=preview_stream_url,
    )


@router.put("/videos/{video_id}/editor/render", response_model=EditorRenderResponse)
@limiter.limit("5/minute")
async def render_library_editor(
    request: Request,
    video_id: int,
    render_request: EditorRenderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Render editor changes for a library video."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    project_dir = _resolve_project_dir_from_video(video)
    if not project_dir:
        raise HTTPException(status_code=400, detail="Project data not available for this video")

    project = editor_service.load_project(project_dir)
    output_path = Path(settings.output_dir) / video.output_filename

    # Use project dir name as lock key to prevent concurrent file mutations
    lock_id = project_dir.name

    def _render_with_lock():
        with project_lock(lock_id):
            return editor_service.render_changes(
                project_dir, project,
                subtitles=render_request.subtitles.model_dump() if render_request.subtitles else None,
                audio=render_request.audio.model_dump() if render_request.audio else None,
                replaced_clips=[c.model_dump() for c in render_request.replaced_clips] if render_request.replaced_clips else None,
                output_path=output_path,
            )

    try:
        loop = asyncio.get_event_loop()
        render_type = await loop.run_in_executor(None, _render_with_lock)
    except HTTPException:
        raise  # Re-raise 409 from lock contention
    except Exception as e:
        logger.exception("Video render failed")
        raise HTTPException(status_code=500, detail="Render failed")

    return EditorRenderResponse(
        status="ok",
        render_type=render_type,
        message=f"Video re-rendered successfully ({render_type})",
    )


@router.get("/videos/{video_id}/editor/preview-stream")
async def stream_video_preview(
    video_id: int,
    request: Request,
    user: User = Depends(get_current_user_from_token_param),
    db: Session = Depends(get_db),
):
    """Stream the subtitle-free preview video for library video editor."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    project_dir = _resolve_project_dir_from_video(video)
    if not project_dir:
        raise HTTPException(status_code=400, detail="Project data not available")

    project = editor_service.load_project(project_dir)
    preview_path = editor_service.generate_preview(project_dir, project)

    if not preview_path or not preview_path.exists():
        raise HTTPException(status_code=404, detail="Preview not available")

    return stream_file_response(preview_path, request)


# ─── Shared clip search ───


@router.post("/editor/search-clips", response_model=List[ClipSearchResult])
async def search_replacement_clips(
    request: ClipSearchRequest,
    user: User = Depends(get_current_user),
):
    """Search Pexels for replacement clip candidates."""
    results = editor_service.search_clips(
        keywords=request.keywords,
        orientation=request.orientation,
        count=request.count,
    )
    return results


# ─── Helpers ───


def _resolve_project_dir_from_session(session) -> Path | None:
    """Find the project directory for a session."""
    if session.project_dir_path:
        project_dir = Path(session.project_dir_path)
        if (project_dir / "project.json").exists():
            return project_dir

    # Try via DB
    if session.user_id and session.output_path:
        from app.db.base import SessionLocal
        db = SessionLocal()
        try:
            video = db.query(Video).filter(
                Video.user_id == session.user_id,
                Video.output_filename == Path(session.output_path).name,
            ).first()
            if video:
                return _resolve_project_dir_from_video(video)
        finally:
            db.close()

    return None


def _resolve_project_dir_from_video(video: Video) -> Path | None:
    """Find the project directory for a library video."""
    # Check the DB field
    if video.project_data_path:
        project_dir = Path(video.project_data_path)
        if (project_dir / "project.json").exists():
            return project_dir

    # Convention-based fallback: projects/<video_id>/
    project_dir = Path(settings.projects_dir) / str(video.id)
    if (project_dir / "project.json").exists():
        return project_dir

    return None
