"""Video library API routes — list, stream, download, delete user's videos."""

import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.db.base import get_db
from app.db.models import User, Video
from app.api.deps import get_current_user, get_current_user_from_token_param
from app.utils.helpers import stream_file_response

router = APIRouter(prefix="/videos", tags=["library"])

# Handlers are plain ``def``: they do synchronous DB queries, R2 calls and file
# deletion, so FastAPI runs them in its threadpool instead of on the event loop.


class VideoItem(BaseModel):
    id: int
    title: str
    status: str
    input_type: str
    narration_style: Optional[str] = None
    duration_seconds: int
    orientation: str
    file_size_bytes: Optional[int] = None
    has_thumbnail: bool = False
    created_at: str


class VideoListResponse(BaseModel):
    videos: List[VideoItem]
    total: int


@router.get("", response_model=VideoListResponse)
def list_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List authenticated user's videos, newest first."""
    query = db.query(Video).filter(Video.user_id == user.id).order_by(Video.created_at.desc())
    total = query.count()
    videos = query.offset((page - 1) * page_size).limit(page_size).all()

    return VideoListResponse(
        videos=[
            VideoItem(
                id=v.id,
                title=v.title,
                status=v.status,
                input_type=v.input_type,
                narration_style=v.narration_style,
                duration_seconds=v.duration_seconds,
                orientation=v.orientation,
                file_size_bytes=v.file_size_bytes,
                has_thumbnail=v.thumbnail_filename is not None,
                created_at=v.created_at.isoformat() if v.created_at else "",
            )
            for v in videos
        ],
        total=total,
    )


def _get_video_path(video: Video) -> Path:
    """Resolve the output file path for a video."""
    return Path(settings.output_dir) / video.output_filename


@router.get("/{video_id}/stream")
def stream_library_video(
    video_id: int,
    request: Request,
    user: User = Depends(get_current_user_from_token_param),
    db: Session = Depends(get_db),
):
    """Stream a video from the user's library with Range support."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.r2_video_key and settings.use_r2_storage:
        from fastapi.responses import RedirectResponse
        from app.api.services.r2_storage import r2_storage
        return RedirectResponse(url=r2_storage.generate_signed_url(video.r2_video_key), status_code=302)

    video_path = _get_video_path(video)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return stream_file_response(video_path, request)


@router.get("/{video_id}/download")
def download_library_video(
    video_id: int,
    user: User = Depends(get_current_user_from_token_param),
    db: Session = Depends(get_db),
):
    """Download a video from the user's library."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.r2_video_key and settings.use_r2_storage:
        from fastapi.responses import RedirectResponse
        from app.api.services.r2_storage import r2_storage
        disposition = f'attachment; filename="{video.title}.mp4"'
        signed_url = r2_storage.generate_signed_url(video.r2_video_key, content_disposition=disposition)
        return RedirectResponse(url=signed_url, status_code=302)

    video_path = _get_video_path(video)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return FileResponse(
        path=video_path,
        filename=f"{video.title}.mp4",
        media_type="video/mp4",
    )


@router.get("/{video_id}/thumbnail")
def get_video_thumbnail(
    video_id: int,
    user: User = Depends(get_current_user_from_token_param),
    db: Session = Depends(get_db),
):
    """Get the video thumbnail image."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.r2_thumbnail_key and settings.use_r2_storage:
        from fastapi.responses import RedirectResponse
        from app.api.services.r2_storage import r2_storage
        return RedirectResponse(url=r2_storage.generate_signed_url(video.r2_thumbnail_key), status_code=302)

    if not video.thumbnail_filename:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    thumb_path = Path(settings.output_dir) / video.thumbnail_filename
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail file not found")

    return FileResponse(path=thumb_path, media_type="image/jpeg")


@router.delete("/{video_id}", status_code=204)
def delete_video(
    video_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a video and its files."""
    import shutil
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Delete all R2 objects (non-fatal if it fails)
    if settings.use_r2_storage:
        from app.api.services.r2_storage import r2_storage, r2_video_prefix
        try:
            r2_storage.delete_prefix(r2_video_prefix(video.user_id, video.id))
        except Exception as e:
            logger.warning("Failed to delete R2 objects for video %d: %s", video_id, e)

    # Delete local output files
    video_path = _get_video_path(video)
    if video_path.exists():
        video_path.unlink()

    if video.thumbnail_filename:
        thumb_path = Path(settings.output_dir) / video.thumbnail_filename
        if thumb_path.exists():
            thumb_path.unlink()

    # Delete local project directory
    if video.project_data_path:
        project_dir = Path(video.project_data_path)
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)

    db.delete(video)
    db.commit()
