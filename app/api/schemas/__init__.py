"""Pydantic schemas for API requests/responses."""

from .session import (
    SessionSettings,
    CreateSessionRequest,
    SessionResponse,
    SessionListResponse,
)
from .script import (
    GenerateScriptRequest,
    UpdateScriptRequest,
    ScriptResponse,
    ConfirmScriptRequest,
)
from .clip import (
    ClipData,
    ClipsResponse,
    UpdateClipRequest,
    ConfirmClipsRequest,
)
from .video import (
    GenerateVideoRequest,
    JobStatusResponse,
    VideoDownloadResponse,
)

__all__ = [
    # Session
    'SessionSettings',
    'CreateSessionRequest',
    'SessionResponse',
    'SessionListResponse',
    # Script
    'GenerateScriptRequest',
    'UpdateScriptRequest',
    'ScriptResponse',
    'ConfirmScriptRequest',
    # Clip
    'ClipData',
    'ClipsResponse',
    'UpdateClipRequest',
    'ConfirmClipsRequest',
    # Video
    'GenerateVideoRequest',
    'JobStatusResponse',
    'VideoDownloadResponse',
]
