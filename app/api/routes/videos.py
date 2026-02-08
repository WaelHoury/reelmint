"""Video generation and download API routes."""

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from pydantic import BaseModel, Field
from typing import Optional

from app.api.schemas import JobStatusResponse, VideoDownloadResponse
from app.api.services.session_store import session_store
from app.api.services.video_service import video_service, regenerate_subtitles
from app.api.deps import get_current_user, get_current_user_from_token_param
from app.db.models import User


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
async def start_video_generation(session_id: str, user: User = Depends(get_current_user)):
    """Start video generation as background job."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != "clips_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Clips must be confirmed first. Current status: {session.status}"
        )

    try:
        job_id = video_service.queue_video_generation(session)
        return JobStatusResponse(
            job_id=job_id,
            status="queued",
            progress_percent=0,
            current_step="Queued for processing...",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=JobStatusResponse)
async def get_job_status(session_id: str, user: User = Depends(get_current_user)):
    """Get video generation job status."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    status_data = video_service.get_job_status(session)

    # Map RQ status to our status
    rq_status = status_data.get('status', session.status)
    if rq_status == 'finished':
        rq_status = 'completed'
    elif rq_status == 'started':
        rq_status = 'processing'

    return JobStatusResponse(
        job_id=status_data.get('job_id'),
        status=rq_status,
        progress_percent=status_data.get('progress_percent') or session.progress_percent,
        current_step=status_data.get('current_step') or session.current_step,
        error_message=status_data.get('error_message'),
        output_url=status_data.get('output_url'),
    )


@router.get("/video")
async def download_video(session_id: str, user: User = Depends(get_current_user_from_token_param)):
    """Download the generated video."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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

    return FileResponse(
        path=video_path,
        filename=f"{session.title}.mp4",
        media_type="video/mp4",
    )


@router.get("/video/stream")
async def stream_video(session_id: str, request: Request, user: User = Depends(get_current_user_from_token_param)):
    """Stream the generated video with Range request support for in-browser playback."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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
    range_header = request.headers.get("range")

    if range_header:
        # Parse Range header: "bytes=start-end"
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

    # No Range header — return full file
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


@router.get("/video/info", response_model=VideoDownloadResponse)
async def get_video_info(session_id: str, user: User = Depends(get_current_user)):
    """Get video download information."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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
async def update_video_subtitles(session_id: str, request: UpdateSubtitlesRequest, user: User = Depends(get_current_user)):
    """Re-generate video subtitles with custom settings."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Video must be completed first. Status: {session.status}"
        )

    try:
        updated = regenerate_subtitles(session, request.model_dump(exclude_none=True))
        return {"status": "ok", "message": "Subtitles updated successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
