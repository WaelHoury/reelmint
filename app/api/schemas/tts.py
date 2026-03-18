"""TTS-related API schemas."""

from typing import List, Optional
from pydantic import BaseModel, Field


class VoiceCatalogItem(BaseModel):
    voice_key: str
    provider: str
    voice_id: str
    label: str
    description: Optional[str] = None
    language: str = "en"
    gender: Optional[str] = None
    grade: Optional[str] = None
    is_cloned: bool = False
    tags: List[str] = []


class VoiceCatalogResponse(BaseModel):
    voices: List[VoiceCatalogItem]
    enabled_providers: List[str]
    default_voice_key: str


class CreateFishVoiceRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    transcript: Optional[str] = Field(default=None, max_length=5000)


class FishVoiceResponse(BaseModel):
    id: int
    fish_voice_id: str
    title: str
    status: str
    voice_key: str
    created_at: str


class FishVoiceListResponse(BaseModel):
    voices: List[FishVoiceResponse]


class UpdateTTSRequest(BaseModel):
    """Request to update TTS selection on a session."""
    provider: str = Field(..., description="TTS provider: kokoro, chatterbox, fish")
    voice_key: str = Field(..., description="Composite key: provider:voice_id")
    label: Optional[str] = Field(default=None, description="Human-readable voice name")
