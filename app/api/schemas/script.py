"""Script schemas for API requests and responses."""

from typing import Optional
from pydantic import BaseModel, Field


class GenerateScriptRequest(BaseModel):
    """Request to generate or provide a script."""
    # For prompt input, this triggers LLM generation
    # For script input, this just validates and stores the script
    pass  # No body needed - uses session data


class UpdateScriptRequest(BaseModel):
    """Request to update/edit the script."""
    script_text: str = Field(..., min_length=10, description="Updated narration script")


class ScriptResponse(BaseModel):
    """Response with script details."""
    script_text: str
    word_count: int
    estimated_duration_seconds: float
    status: str


class ConfirmScriptRequest(BaseModel):
    """Request to confirm script is ready."""
    pass  # No body needed
