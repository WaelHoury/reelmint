"""Clip schemas for API requests and responses."""

from typing import List, Optional
from pydantic import BaseModel, Field


class ClipData(BaseModel):
    """Data for a single visual clip."""
    index: int = Field(..., ge=0, description="Clip position in video")
    keywords: List[str] = Field(..., min_length=1, description="Search keywords")
    visual_description: str = Field(..., description="Description of what viewer sees")
    video_preview_url: Optional[str] = Field(default=None, description="Pexels video preview URL")
    video_id: Optional[str] = Field(default=None, description="Pexels video ID")
    author: Optional[str] = Field(default=None, description="Video author/photographer")
    author_url: Optional[str] = Field(default=None, description="Author profile URL")
    duration_seconds: Optional[float] = Field(default=None, description="Clip duration")


class ClipsResponse(BaseModel):
    """Response with all clips."""
    clips: List[ClipData]
    total_clips: int
    status: str


class UpdateClipRequest(BaseModel):
    """Request to update a single clip's description."""
    visual_description: str = Field(..., min_length=5, description="New visual description")
    keywords: Optional[List[str]] = Field(default=None, description="Optional new keywords")


class ConfirmClipsRequest(BaseModel):
    """Request to confirm all clips are ready."""
    pass  # No body needed
