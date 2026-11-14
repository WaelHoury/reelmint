"""Clip schemas for API requests and responses."""

from typing import List, Optional
from pydantic import BaseModel, Field


class ClipData(BaseModel):
    """Data for a single visual clip."""
    index: int = Field(..., ge=0, description="Clip position in video")
    keywords: List[str] = Field(..., min_length=1, description="Search keywords")
    visual_description: str = Field(..., description="Description of what viewer sees")
    video_preview_url: Optional[str] = Field(default=None, description="Preview URL (video, or still image when media_type is image)")
    video_id: Optional[str] = Field(default=None, description="Provider media ID")
    author: Optional[str] = Field(default=None, description="Video author/photographer")
    author_url: Optional[str] = Field(default=None, description="Author profile URL")
    duration_seconds: Optional[float] = Field(default=None, description="Clip duration")
    search_status: str = Field(default="pending", description="Search status: pending, ready, failed")
    search_error: Optional[str] = Field(default=None, description="Error message if search failed")
    media_type: Optional[str] = Field(default=None, description="Media type: video or image")
    provider: Optional[str] = Field(default=None, description="Stock media provider: Pexels or Pixabay")


class ClipsResponse(BaseModel):
    """Response with all clips."""
    clips: List[ClipData]
    total_clips: int
    status: str
    completed_clips: int = Field(default=0, description="Number of clips with completed searches")
    progress_percent: int = Field(default=0, description="Overall clip generation progress (0-100)")


class UpdateClipRequest(BaseModel):
    """Request to update a single clip's description."""
    visual_description: str = Field(..., min_length=5, description="New visual description")
    keywords: Optional[List[str]] = Field(default=None, description="Optional new keywords")


class ConfirmClipsRequest(BaseModel):
    """Request to confirm all clips are ready."""
    pass  # No body needed
