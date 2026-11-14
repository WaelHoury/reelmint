"""Pydantic schemas for the video editor API."""

import re
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

# Upper bound for any timeline time (sanity; real bounds are checked server-side)
MAX_SECONDS = 6 * 3600


def _normalize_color(value: Optional[str]) -> Optional[str]:
    """#RGB / #RRGGBB / rgb() / rgba() -> #RRGGBB; None means "use the preset"."""
    if value is None or value == "":
        return None
    from subtitle_generator import SubtitleGenerator
    try:
        return SubtitleGenerator.normalize_color(value)
    except ValueError:
        raise ValueError(f"{value!r} is not a colour; use #RRGGBB")


def _check_font(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    if not re.fullmatch(r"[A-Za-z0-9 _.\-]{1,64}", value):
        raise ValueError("Font name may only contain letters, digits, spaces, '-', '_' and '.'")
    return value


class EditorClipInfo(BaseModel):
    """A video clip in the editor timeline."""
    index: int
    keywords: List[str] = []
    visual_description: str = ""
    video_id: Optional[str] = None
    provider: Optional[str] = "Pexels"
    media_type: Optional[str] = "video"
    author: Optional[str] = None
    duration_seconds: float = 0
    start_time: float = 0
    end_time: float = 0
    trim_start: float = 0
    trim_end: float = 0
    pad_seconds: float = 0
    loop_copy: bool = False


class EditorAudioInfo(BaseModel):
    """Audio settings for the editor."""
    narration_volume: float = 1.0
    music_volume: float = 0.15
    has_background_music: bool = False


class SubtitleEntry(BaseModel):
    """A single subtitle entry on the timeline."""
    index: int = Field(ge=0)
    text: str = Field(max_length=500)
    start_time: float = Field(ge=0, le=MAX_SECONDS)
    end_time: float = Field(ge=0, le=MAX_SECONDS)

    @model_validator(mode="after")
    def _start_before_end(self):
        if self.end_time <= self.start_time:
            raise ValueError(
                f"Caption {self.index + 1}: end time ({self.end_time:.2f}s) must be after "
                f"start time ({self.start_time:.2f}s)")
        return self


class EditorSubtitleInfo(BaseModel):
    """Subtitle settings and entries for the editor."""
    enabled: bool = True
    style: str = "youtube"
    font: str = "Arial"
    font_size: int = 52
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    position: str = "bottom"
    # Fields the user explicitly set (the rest come from the style preset)
    overrides: List[str] = []
    # Resolved values of each preset for this video's orientation
    presets: Dict[str, Dict] = {}
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
    """Subtitle settings for a render request.

    font / font_size / color / outline_color / position are overrides: send
    only the ones the user changed; omitted ones come from the style preset.
    Send ``entries`` only when caption text or timing was edited.
    """
    enabled: bool = True
    style: Literal["youtube", "tiktok", "minimal"] = "youtube"
    font: Optional[str] = None
    font_size: Optional[int] = Field(default=None, ge=12, le=160)
    color: Optional[str] = None
    outline_color: Optional[str] = None
    position: Optional[Literal["bottom", "center", "top"]] = None
    entries: Optional[List[SubtitleEntry]] = Field(default=None, max_length=5000)

    @field_validator("color", "outline_color")
    @classmethod
    def _color(cls, v):
        return _normalize_color(v)

    @field_validator("font")
    @classmethod
    def _font(cls, v):
        return _check_font(v)


class EditorAudioSettings(BaseModel):
    """Audio settings for a render request."""
    narration_volume: float = Field(default=1.0, ge=0, le=2)
    music_volume: float = Field(default=0.15, ge=0, le=1)


class ReplacedClip(BaseModel):
    """A clip edit (replacement and/or trim) applied during render.

    Trims are checked against the clip's length server-side (400 if they
    leave less than 0.5s of footage).
    """
    index: int = Field(ge=0)
    keywords: List[str] = []
    video_id: Optional[str] = None
    provider: Optional[str] = None
    media_type: Optional[Literal["video", "image"]] = None
    trim_start: float = Field(default=0, ge=0, le=MAX_SECONDS)
    trim_end: float = Field(default=0, ge=0, le=MAX_SECONDS)


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


class ExcludedClip(BaseModel):
    """A clip already used in the video (not offered again)."""
    video_id: str
    provider: Optional[str] = None


class ClipSearchRequest(BaseModel):
    """Request to search for replacement clips."""
    keywords: List[str] = Field(min_length=1, max_length=20)
    orientation: str = "landscape"
    count: int = Field(default=6, ge=1, le=20)
    # Slot length: clips at least this long are preferred (shorter ones get frozen)
    min_duration: float = Field(default=0, ge=0, le=600)
    exclude: List[ExcludedClip] = Field(default=[], max_length=500)
    topic: Optional[str] = Field(default=None, max_length=300)


class ClipSearchResult(BaseModel):
    """A single clip search result."""
    video_id: str
    provider: str = "Pexels"
    media_type: str = "video"
    preview_url: str
    author: str = ""
    duration_seconds: float = 0
    width: int = 0
    height: int = 0
