"""Redis-based session storage for video generation sessions."""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from dataclasses import dataclass, field, asdict
import redis

from app.core.config import settings


@dataclass
class ClipInfo:
    """Information about a visual clip."""
    index: int
    keywords: List[str]
    visual_description: str
    video_preview_url: Optional[str] = None
    video_id: Optional[str] = None
    author: Optional[str] = None
    author_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    # Internal: path to downloaded video (not exposed to API)
    local_path: Optional[str] = None


@dataclass
class VideoSession:
    """Video generation session data."""
    session_id: str
    created_at: str  # ISO format
    status: Literal["draft", "script_ready", "clips_ready", "generating", "completed", "failed"]

    # User settings
    title: str
    duration_seconds: int
    loop_mode: bool
    subtitles_enabled: bool
    orientation: Literal["landscape", "portrait", "square"]

    # Input
    input_type: Literal["prompt", "script"]
    prompt: Optional[str] = None
    script_text: Optional[str] = None
    narration_style: Optional[str] = None

    # Computed
    word_count: Optional[int] = None
    estimated_duration_seconds: Optional[float] = None

    # Generated data
    clips: List[Dict] = field(default_factory=list)  # List of ClipInfo as dicts

    # Job tracking
    job_id: Optional[str] = None
    progress_percent: Optional[int] = None
    current_step: Optional[str] = None

    # Subtitle customization
    subtitle_style: Optional[str] = None
    subtitle_font: Optional[str] = None
    subtitle_font_size: Optional[int] = None
    subtitle_color: Optional[str] = None
    subtitle_outline_color: Optional[str] = None
    subtitle_position: Optional[str] = None

    # Intermediate files for subtitle re-generation
    temp_dir_path: Optional[str] = None
    video_no_audio_path: Optional[str] = None
    final_audio_path: Optional[str] = None
    word_timestamps: Optional[List[Dict]] = None

    # Auth
    user_id: Optional[int] = None

    # Output
    output_path: Optional[str] = None
    credits_path: Optional[str] = None
    error_message: Optional[str] = None


class SessionStore:
    """Redis-based session storage."""

    def __init__(self, redis_url: str = None):
        """Initialize session store.

        Args:
            redis_url: Redis connection URL (default from settings)
        """
        self.redis_url = redis_url or settings.redis_url
        self._redis: Optional[redis.Redis] = None
        self.ttl_seconds = settings.session_ttl_hours * 3600
        self.key_prefix = "video_session:"

    @property
    def redis(self) -> redis.Redis:
        """Lazy Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    def _key(self, session_id: str) -> str:
        """Get Redis key for session."""
        return f"{self.key_prefix}{session_id}"

    def create(
        self,
        title: str,
        input_type: Literal["prompt", "script"],
        prompt: Optional[str] = None,
        script_text: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        loop_mode: bool = False,
        subtitles_enabled: bool = True,
        orientation: str = "landscape",
        narration_style: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> VideoSession:
        """Create a new session.

        Args:
            title: Video title
            input_type: 'prompt' or 'script'
            prompt: Topic for LLM generation (required if input_type='prompt')
            script_text: Pre-written script (required if input_type='script')
            duration_seconds: Video duration (required for prompt, calculated for script)
            loop_mode: Enable seamless loop
            subtitles_enabled: Enable subtitles
            orientation: Video orientation

        Returns:
            New VideoSession

        Raises:
            ValueError: If required fields are missing
        """
        if input_type == "prompt" and not prompt:
            raise ValueError("prompt is required when input_type is 'prompt'")
        if input_type == "script" and not script_text:
            raise ValueError("script_text is required when input_type is 'script'")
        if input_type == "prompt" and not duration_seconds:
            raise ValueError("duration_seconds is required when input_type is 'prompt'")

        session_id = str(uuid.uuid4())

        # Calculate word count and duration for script input
        word_count = None
        estimated_duration = None
        if script_text:
            word_count = len(script_text.split())
            # Estimate duration from word count (3 words per second)
            from app.core.config import get_words_per_second
            wps = get_words_per_second()
            estimated_duration = word_count / wps
            if duration_seconds is None:
                duration_seconds = int(estimated_duration)

        session = VideoSession(
            session_id=session_id,
            created_at=datetime.utcnow().isoformat(),
            status="draft",
            title=title,
            duration_seconds=duration_seconds,
            loop_mode=loop_mode,
            subtitles_enabled=subtitles_enabled,
            orientation=orientation,
            input_type=input_type,
            prompt=prompt,
            script_text=script_text,
            narration_style=narration_style,
            word_count=word_count,
            estimated_duration_seconds=estimated_duration,
            user_id=user_id,
        )

        self._save(session)
        return session

    def get(self, session_id: str) -> Optional[VideoSession]:
        """Get session by ID.

        Args:
            session_id: Session ID

        Returns:
            VideoSession or None if not found
        """
        data = self.redis.get(self._key(session_id))
        if not data:
            return None
        return self._deserialize(data)

    def update(self, session: VideoSession) -> None:
        """Update session.

        Args:
            session: Session to update
        """
        self._save(session)

    def delete(self, session_id: str) -> bool:
        """Delete session.

        Args:
            session_id: Session ID

        Returns:
            True if deleted, False if not found
        """
        return bool(self.redis.delete(self._key(session_id)))

    def list_all(self, limit: int = 100) -> List[VideoSession]:
        """List all sessions.

        Args:
            limit: Maximum sessions to return

        Returns:
            List of sessions
        """
        keys = self.redis.keys(f"{self.key_prefix}*")
        sessions = []
        for key in keys[:limit]:
            data = self.redis.get(key)
            if data:
                sessions.append(self._deserialize(data))
        return sorted(sessions, key=lambda s: s.created_at, reverse=True)

    def _save(self, session: VideoSession) -> None:
        """Save session to Redis."""
        data = self._serialize(session)
        self.redis.setex(self._key(session.session_id), self.ttl_seconds, data)

    def _serialize(self, session: VideoSession) -> str:
        """Serialize session to JSON."""
        return json.dumps(asdict(session))

    def _deserialize(self, data: str) -> VideoSession:
        """Deserialize session from JSON."""
        d = json.loads(data)
        return VideoSession(**d)


# Global session store instance
session_store = SessionStore()
