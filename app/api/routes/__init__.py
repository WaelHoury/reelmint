"""API route modules."""

from .sessions import router as sessions_router
from .scripts import router as scripts_router
from .clips import router as clips_router
from .videos import router as videos_router
from .config import router as config_router
from .auth import router as auth_router
from .library import router as library_router
from .editor import router as editor_router
from .tts import router as tts_router
from .publish import router as publish_router
from .publish import video_publish_router

__all__ = [
    'sessions_router',
    'scripts_router',
    'clips_router',
    'videos_router',
    'config_router',
    'auth_router',
    'library_router',
    'editor_router',
    'tts_router',
    'publish_router',
    'video_publish_router',
]
