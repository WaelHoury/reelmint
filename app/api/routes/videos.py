"""Video generation and download API routes."""

from pathlib import Path
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.api.schemas import JobStatusResponse, VideoDownloadResponse
from app.api.services.session_store import session_store
from app.api.services.video_service import video_service

router = APIRouter(prefix="/sessions/{session_id}", tags=["videos"])


@router.post("/generate", response_model=JobStatusResponse)
async def start_video_generation(session_id: str):
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
async def get_job_status(session_id: str):
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
async def download_video(session_id: str):
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


@router.get("/video/info", response_model=VideoDownloadResponse)
async def get_video_info(session_id: str):
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
