"""Video generation and download API routes."""

import asyncio
import functools
import logging

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field
from typing import Optional

from app.api.schemas import JobStatusResponse, VideoDownloadResponse
from app.api.services.session_store import session_store
from app.api.services.video_service import video_service, regenerate_subtitles
from app.api.deps import get_current_user, get_current_user_from_token_param, get_owned_session, is_superuser
from app.db.models import User
from app.utils.helpers import stream_file_response, project_lock
from app.api.limiter import limiter


def _reupload_video_to_r2(session) -> None:
    """Upload the (re-rendered) video file to R2 with a bumped version key.

    Updates render_version and r2_video_key in the DB.
    Non-fatal: logs a warning on failure so the subtitle update still succeeds.
    """
    from app.core.config import settings
    from app.db.base import SessionLocal
    from app.db.models import Video
    from app.api.services.r2_storage import r2_storage, r2_video_key

    try:
        output_path = Path(session.output_path)
        if not output_path.exists():
            logger.warning("Cannot re-upload to R2: video file missing at %s", output_path)
            return
        db = SessionLocal()
        try:
            video = db.query(Video).filter(
                Video.user_id == session.user_id,
                Video.output_filename == output_path.name,
            ).first()
            if not video:
                return
            new_version = (video.render_version or 1) + 1
            new_key = r2_video_key(video.user_id, video.id, version=new_version)
            r2_storage.upload_file(output_path, new_key)
            video.r2_video_key = new_key
            video.render_version = new_version
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        logger.warning("R2 re-upload after subtitle regen failed: %s", e)


class UpdateSubtitlesRequest(BaseModel):
    """Request to update subtitle settings and re-generate."""
    style: Optional[str] = Field(default="youtube", description="Preset: youtube, tiktok, minimal")
    font: Optional[str] = Field(default=None, description="Font name")
    font_size: Optional[int] = Field(default=None, ge=24, le=96, description="Font size")
    color: Optional[str] = Field(default=None, description="Text color hex (#RRGGBB)")
    outline_color: Optional[str] = Field(default=None, description="Outline color hex (#RRGGBB)")
    position: Optional[str] = Field(default=None, description="Position: bottom, center, top")

router = APIRouter(prefix="/sessions/{session_id}", tags=["videos"])


@router.post("/generate", response_model=JobStatusResponse)
@limiter.limit("3/minute")
async def start_video_generation(request: Request, session_id: str, user: User = Depends(get_current_user)):
    """Start video generation as background job.

    Idempotent: if generation is already running for this session, returns
    current job status instead of re-enqueuing.
    """
    session = get_owned_session(session_id, user)

    # If already generating, return current status (idempotent on refresh)
    if session.status == "generating" and session.job_id:
        status_data = video_service.get_job_status(session)
        return JobStatusResponse(
            job_id=session.job_id,
            status=status_data.get('status', 'processing'),
            progress_percent=status_data.get('progress_percent') or session.progress_percent or 0,
            current_step=status_data.get('current_step') or session.current_step or 'Processing...',
        )

    if session.status != "clips_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Clips must be confirmed first. Current status: {session.status}"
        )

    try:
        job_id = video_service.queue_video_generation(session, is_superuser=is_superuser(user))
        return JobStatusResponse(
            job_id=job_id,
            status="queued",
            progress_percent=0,
            current_step="Queued for processing...",
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("Failed to queue video generation for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to queue video generation")


@router.get("/status", response_model=JobStatusResponse)
async def get_job_status(session_id: str, user: User = Depends(get_current_user)):
    """Get video generation job status."""
    session = get_owned_session(session_id, user)

    status_data = video_service.get_job_status(session)

    return JobStatusResponse(
        job_id=status_data.get('job_id'),
        status=status_data.get('status', 'queued'),
        progress_percent=status_data.get('progress_percent') or session.progress_percent,
        current_step=status_data.get('current_step') or session.current_step,
        error_message=status_data.get('error_message'),
        output_url=status_data.get('output_url'),
    )


def _get_r2_video_key_for_session(session):
    """Look up the R2 video key for a session via the DB Video record."""
    from app.core.config import settings
    if not settings.use_r2_storage or not session.user_id or not session.output_path:
        return None
    from app.db.base import SessionLocal
    from app.db.models import Video
    from pathlib import Path as _Path
    db = SessionLocal()
    try:
        video = db.query(Video).filter(
            Video.user_id == session.user_id,
            Video.output_filename == _Path(session.output_path).name,
        ).first()
        return (video.r2_video_key, video.id, video.render_version) if video else None
    except Exception:
        return None
    finally:
        db.close()


@router.get("/video")
async def download_video(session_id: str, user: User = Depends(get_current_user_from_token_param)):
    """Download the generated video."""
    session = get_owned_session(session_id, user)

    if session.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Video not ready. Status: {session.status}"
        )

    if not session.output_path:
        raise HTTPException(status_code=404, detail="Video file not found")

    r2_result = _get_r2_video_key_for_session(session)
    if r2_result and r2_result[0]:
        from fastapi.responses import RedirectResponse
        from app.api.services.r2_storage import r2_storage
        disposition = f'attachment; filename="{session.title}.mp4"'
        return RedirectResponse(
            url=r2_storage.generate_signed_url(r2_result[0], content_disposition=disposition),
            status_code=302,
        )

    video_path = Path(session.output_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return FileResponse(
        path=video_path,
        filename=f"{session.title}.mp4",
        media_type="video/mp4",
    )


@router.get("/video/stream")
async def stream_video(session_id: str, request: Request, user: User = Depends(get_current_user_from_token_param)):
    """Stream the generated video with Range request support for in-browser playback."""
    session = get_owned_session(session_id, user)

    if session.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Video not ready. Status: {session.status}"
        )

    if not session.output_path:
        raise HTTPException(status_code=404, detail="Video file not found")

    r2_result = _get_r2_video_key_for_session(session)
    if r2_result and r2_result[0]:
        from fastapi.responses import RedirectResponse
        from app.api.services.r2_storage import r2_storage
        return RedirectResponse(url=r2_storage.generate_signed_url(r2_result[0]), status_code=302)

    video_path = Path(session.output_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return stream_file_response(video_path, request)


@router.get("/video/info", response_model=VideoDownloadResponse)
async def get_video_info(session_id: str, user: User = Depends(get_current_user)):
    """Get video download information."""
    session = get_owned_session(session_id, user)

    if session.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Video not ready. Status: {session.status}"
        )

    if not session.output_path:
        raise HTTPException(status_code=404, detail="Video file not found")

    video_path = Path(session.output_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    file_size = video_path.stat().st_size

    return VideoDownloadResponse(
        download_url=f"/api/sessions/{session_id}/video",
        filename=f"{session.title}.mp4",
        file_size_bytes=file_size,
        duration_seconds=session.estimated_duration_seconds,
    )


@router.put("/subtitles")
@limiter.limit("5/minute")
async def update_video_subtitles(request: Request, session_id: str, sub_request: UpdateSubtitlesRequest, user: User = Depends(get_current_user)):
    """Re-generate video subtitles with custom settings."""
    session = get_owned_session(session_id, user)

    if session.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Video must be completed first. Status: {session.status}"
        )

    def _regen_with_lock():
        with project_lock(session_id):
            regenerate_subtitles(session, sub_request.model_dump(exclude_none=True))
            # Re-upload updated video to R2 if enabled
            from app.core.config import settings as _settings
            if _settings.use_r2_storage and session.output_path:
                _reupload_video_to_r2(session)

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _regen_with_lock)
        return {"status": "ok", "message": "Subtitles updated successfully"}
    except HTTPException:
        raise  # Re-raise 409 from lock contention
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Subtitle update failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Subtitle update failed")
