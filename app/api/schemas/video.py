"""Video generation schemas for API requests and responses."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class GenerateVideoRequest(BaseModel):
    """Request to start video generation."""
    pass  # No body needed - uses session data


class JobStatusResponse(BaseModel):
    """Response with video generation job status."""
    job_id: Optional[str] = Field(default=None, description="RQ job ID")
    status: Literal["queued", "processing", "completed", "failed"] = Field(
        ..., description="Current job status"
    )
    progress_percent: Optional[int] = Field(default=None, ge=0, le=100)
    current_step: Optional[str] = Field(default=None, description="Current processing step")
    estimated_remaining_seconds: Optional[int] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    output_url: Optional[str] = Field(default=None, description="URL to download video when completed")


class VideoDownloadResponse(BaseModel):
    """Response with video download information."""
    download_url: str
    filename: str
    file_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None
