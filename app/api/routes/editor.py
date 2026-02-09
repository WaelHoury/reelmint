"""Video editor API routes — load editor data, render changes, search clips."""

import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import get_db
from app.db.models import User, Video
from app.api.deps import get_current_user, get_current_user_from_token_param
from app.api.services.session_store import session_store
from app.api.services.editor_service import editor_service
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
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your session")
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
async def render_session_editor(
    session_id: str,
    request: EditorRenderRequest,
    user: User = Depends(get_current_user),
):
    """Render editor changes for a session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your session")

    project_dir = _resolve_project_dir_from_session(session)
    if not project_dir:
        raise HTTPException(status_code=400, detail="Project data not available for this video")

    project = editor_service.load_project(project_dir)
    output_path = Path(session.output_path) if session.output_path else None
    if not output_path:
        raise HTTPException(status_code=400, detail="No output path for this session")

    try:
        render_type = editor_service.render_changes(
            project_dir, project,
            subtitles=request.subtitles.model_dump() if request.subtitles else None,
            audio=request.audio.model_dump() if request.audio else None,
            replaced_clips=[c.model_dump() for c in request.replaced_clips] if request.replaced_clips else None,
            output_path=output_path,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {str(e)}")

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
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your session")

    project_dir = _resolve_project_dir_from_session(session)
    if not project_dir:
        raise HTTPException(status_code=400, detail="Project data not available")

    project = editor_service.load_project(project_dir)
    preview_path = editor_service.generate_preview(project_dir, project)

    if not preview_path or not preview_path.exists():
        raise HTTPException(status_code=404, detail="Preview not available")

    return _stream_file(preview_path, request)


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
async def render_library_editor(
    video_id: int,
    request: EditorRenderRequest,
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

    try:
        render_type = editor_service.render_changes(
            project_dir, project,
            subtitles=request.subtitles.model_dump() if request.subtitles else None,
            audio=request.audio.model_dump() if request.audio else None,
            replaced_clips=[c.model_dump() for c in request.replaced_clips] if request.replaced_clips else None,
            output_path=output_path,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {str(e)}")

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

    return _stream_file(preview_path, request)


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


def _stream_file(file_path: Path, request: Request):
    """Stream a file with HTTP Range support and cache-control headers."""
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    headers_base = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache, no-store, must-revalidate",
    }

    if range_header:
        range_val = range_header.strip().split("=")[1]
        range_parts = range_val.split("-")
        start = int(range_parts[0])
        end = int(range_parts[1]) if range_parts[1] else file_size - 1
        end = min(end, file_size - 1)
        chunk_size = end - start + 1

        def iter_file():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = f.read(min(8192, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_file(),
            status_code=206,
            media_type="video/mp4",
            headers={
                **headers_base,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(chunk_size),
            },
        )

    def iter_full():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        iter_full(),
        media_type="video/mp4",
        headers={
            **headers_base,
            "Content-Length": str(file_size),
        },
    )


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
