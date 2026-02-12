"""Session schemas for API requests and responses."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class SessionSettings(BaseModel):
    """Video generation settings."""
    duration_seconds: int = Field(..., gt=0, description="Video duration in seconds")
    loop_mode: bool = Field(default=False, description="Enable seamless loop")
    subtitles_enabled: bool = Field(default=True, description="Enable subtitles")
    orientation: Literal["landscape", "portrait", "square"] = Field(
        default="landscape", description="Video orientation"
    )


class CreateSessionRequest(BaseModel):
    """Request to create a new video generation session."""
    title: str = Field(..., min_length=1, max_length=200, description="Video title (used as filename)")
    input_type: Literal["prompt", "script"] = Field(..., description="Input type: prompt or script")
    prompt: Optional[str] = Field(default=None, description="Topic/prompt for LLM to generate script")
    script: Optional[str] = Field(default=None, description="Pre-written narration script")
    duration_seconds: Optional[int] = Field(default=None, gt=0, description="Video duration (required for prompt, optional for script)")
    loop_mode: bool = Field(default=False)
    subtitles_enabled: bool = Field(default=True)
    orientation: Literal["landscape", "portrait", "square"] = Field(default="landscape")
    narration_style: Optional[str] = Field(default=None, description="Narration style for prompt mode (viral, informative, funny, storytelling, motivational, creative)")

    @field_validator("title")
    @classmethod
    def validate_title_safe(cls, v: str) -> str:
        """Reject titles containing path traversal characters."""
        if any(c in v for c in ("/", "\\", "\0")):
            raise ValueError("Title must not contain path separator characters")
        if ".." in v:
            raise ValueError("Title must not contain '..'")
        return v.strip()


class SessionResponse(BaseModel):
    """Response with session details."""
    session_id: str
    status: Literal["draft", "script_ready", "clips_generating", "clips_ready", "generating", "completed", "failed"]
    title: str
    created_at: datetime
    input_type: Literal["prompt", "script"]
    settings: SessionSettings
    script_text: Optional[str] = None
    word_count: Optional[int] = None
    estimated_duration_seconds: Optional[float] = None
    error_message: Optional[str] = None


class SessionListResponse(BaseModel):
    """Response with list of sessions."""
    sessions: List[SessionResponse]
    total: int
