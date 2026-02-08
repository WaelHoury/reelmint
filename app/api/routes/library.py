"""Video library API routes — list, stream, download, delete user's videos."""

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import get_db
from app.db.models import User, Video
from app.api.deps import get_current_user, get_current_user_from_token_param

router = APIRouter(prefix="/videos", tags=["library"])


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
async def list_videos(
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
async def stream_library_video(
    video_id: int,
    request: Request,
    user: User = Depends(get_current_user_from_token_param),
    db: Session = Depends(get_db),
):
    """Stream a video from the user's library with Range support."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video_path = _get_video_path(video)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    file_size = video_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        range_val = range_header.strip().split("=")[1]
        range_parts = range_val.split("-")
        start = int(range_parts[0])
        end = int(range_parts[1]) if range_parts[1] else file_size - 1
        end = min(end, file_size - 1)
        chunk_size = end - start + 1

        def iter_file():
            with open(video_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    read_size = min(8192, remaining)
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_file(),
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            },
        )

    def iter_full():
        with open(video_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        iter_full(),
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@router.get("/{video_id}/download")
async def download_library_video(
    video_id: int,
    user: User = Depends(get_current_user_from_token_param),
    db: Session = Depends(get_db),
):
    """Download a video from the user's library."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video_path = _get_video_path(video)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return FileResponse(
        path=video_path,
        filename=f"{video.title}.mp4",
        media_type="video/mp4",
    )


@router.get("/{video_id}/thumbnail")
async def get_video_thumbnail(
    video_id: int,
    user: User = Depends(get_current_user_from_token_param),
    db: Session = Depends(get_db),
):
    """Get the video thumbnail image."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.thumbnail_filename:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    thumb_path = Path(settings.output_dir) / video.thumbnail_filename
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail file not found")

    return FileResponse(path=thumb_path, media_type="image/jpeg")


@router.delete("/{video_id}", status_code=204)
async def delete_video(
    video_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a video and its files."""
    video = db.query(Video).filter(Video.id == video_id, Video.user_id == user.id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Delete files
    video_path = _get_video_path(video)
    if video_path.exists():
        video_path.unlink()

    if video.thumbnail_filename:
        thumb_path = Path(settings.output_dir) / video.thumbnail_filename
        if thumb_path.exists():
            thumb_path.unlink()

    db.delete(video)
    db.commit()
