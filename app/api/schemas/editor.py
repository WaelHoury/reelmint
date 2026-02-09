"""Pydantic schemas for the video editor API."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class EditorClipInfo(BaseModel):
    """A video clip in the editor timeline."""
    index: int
    keywords: List[str] = []
    visual_description: str = ""
    video_id: Optional[str] = None
    author: Optional[str] = None
    duration_seconds: float = 0
    start_time: float = 0
    end_time: float = 0
    trim_start: float = 0
    trim_end: float = 0


class EditorAudioInfo(BaseModel):
    """Audio settings for the editor."""
    narration_volume: float = 1.0
    music_volume: float = 0.15
    has_background_music: bool = False


class SubtitleEntry(BaseModel):
    """A single subtitle entry on the timeline."""
    index: int
    text: str
    start_time: float
    end_time: float


class EditorSubtitleInfo(BaseModel):
    """Subtitle settings and entries for the editor."""
    enabled: bool = True
    style: str = "youtube"
    font: str = "Arial"
    font_size: int = 52
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    position: str = "bottom"
    entries: List[SubtitleEntry] = []


class EditorDataResponse(BaseModel):
    """Full editor state returned to the frontend."""
    session_id: Optional[str] = None
    video_id: Optional[int] = None
    title: str
    orientation: str
    duration_seconds: float
    stream_url: str
    preview_stream_url: Optional[str] = None
    script_text: Optional[str] = None
    clips: List[EditorClipInfo] = []
    audio: EditorAudioInfo = EditorAudioInfo()
    subtitles: EditorSubtitleInfo = EditorSubtitleInfo()
    word_timestamps: Optional[List[Dict]] = None


# --- Render request schemas ---

class EditorSubtitleSettings(BaseModel):
    """Subtitle settings for a render request."""
    enabled: bool = True
    style: str = "youtube"
    font: str = "Arial"
    font_size: int = 52
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    position: str = "bottom"
    entries: Optional[List[SubtitleEntry]] = None


class EditorAudioSettings(BaseModel):
    """Audio settings for a render request."""
    narration_volume: float = 1.0
    music_volume: float = 0.15


class ReplacedClip(BaseModel):
    """A clip to be replaced during render."""
    index: int
    keywords: List[str]
    video_id: Optional[str] = None
    trim_start: float = 0
    trim_end: float = 0


class EditorRenderRequest(BaseModel):
    """Request to render editor changes."""
    subtitles: Optional[EditorSubtitleSettings] = None
    audio: Optional[EditorAudioSettings] = None
    replaced_clips: Optional[List[ReplacedClip]] = None


class EditorRenderResponse(BaseModel):
    """Response from a render request."""
    status: str = "ok"
    render_type: str = ""
    message: str = ""


class ClipSearchRequest(BaseModel):
    """Request to search for replacement clips."""
    keywords: List[str]
    orientation: str = "landscape"
    count: int = Field(default=6, ge=1, le=20)


class ClipSearchResult(BaseModel):
    """A single clip search result."""
    video_id: str
    preview_url: str
    author: str = ""
    duration_seconds: float = 0
    width: int = 0
    height: int = 0
