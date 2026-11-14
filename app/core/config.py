"""Centralized configuration and settings for AI Video Maker."""

import os
from pathlib import Path
from typing import Literal, Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load .env file
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # === Paths ===
    base_dir: Path = Path(__file__).parent.parent.parent
    output_dir: Path = Path("output")
    temp_dir: Path = Path("temp")
    projects_dir: Path = Path("projects")
    cache_dir: Path = Path("cache")
    music_dir: Path = Path("music")
    models_dir: Path = Path("models")

    # === LLM Provider ===
    llm_provider: Literal["ollama", "openrouter", "anthropic", "claude-code"] = "ollama"
    narration_style: str = "viral"

    # Anthropic API (llm_provider=anthropic)
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"

    # Claude Code CLI (llm_provider=claude-code) - uses the CLI's own login,
    # e.g. a Claude Pro/Max subscription. No API key.
    claude_code_path: str = "claude"
    claude_code_model: Optional[str] = None

    # Ollama
    ollama_model: str = "qwen2.5:3b"
    ollama_base_url: str = "http://localhost:11434"

    # OpenRouter
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "google/gemini-2.0-flash-001"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # === TTS ===
    tts_engine: Literal["kokoro", "piper", "orpheus", "chatterbox"] = "kokoro"  # Legacy fallback
    words_per_second: Optional[float] = None  # Override, or calculated from engine

    # Provider-based TTS (new)
    tts_enabled_providers: str = "kokoro"  # Comma-separated: kokoro,chatterbox,fish
    tts_default_provider: str = "kokoro"
    tts_default_voice_key: str = "kokoro:af_heart"  # provider:voice_id
    tts_preview_text: str = "Welcome to AI Video Maker. This is a preview of how your narration will sound."

    # Kokoro
    kokoro_voice: str = "af_heart"
    kokoro_speed: float = 1.0
    kokoro_lang: str = "a"

    # Piper
    piper_executable: str = "piper.exe" if os.name == "nt" else "piper"
    piper_model_path: Optional[str] = None

    # Orpheus
    orpheus_voice: str = "tara"

    # Chatterbox
    chatterbox_exaggeration: float = 0.5
    chatterbox_cfg: float = 0.5
    chatterbox_voice_ref: str = ""

    # Fish Audio
    fish_audio_api_key: str = ""
    fish_audio_model: str = "s2-pro"
    fish_preset_voice_ids: str = ""  # Comma-separated Fish voice IDs for app-curated presets
    fish_audio_words_per_second: float = 3.0
    fish_public_voices_enabled: bool = False
    fish_public_voice_page_size: int = 24
    fish_public_voice_page_number: int = 1
    fish_public_voice_sort_by: str = "score"
    fish_public_voice_cache_seconds: int = 900

    # === Video Search (Pexels) ===
    pexels_api_key: str = ""
    clip_search_workers: int = 6
    clip_search_cache_ttl: int = 21600  # 6 hours

    # === Video Search (Pixabay) ===
    pixabay_api_key: str = ""
    enable_pixabay: bool = False
    enable_image_fallback: bool = False

    # === CLIP Matching ===
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    clip_candidates: int = 3

    # === FFmpeg ===
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffmpeg_threads: int = 4
    # Final output encoding (used by concat_with_transitions, finalize_video)
    ffmpeg_preset: str = "fast"
    ffmpeg_crf: int = 25
    # Intermediate encoding (used by process_clip, normalize, trim — re-encoded later)
    ffmpeg_intermediate_preset: str = "veryfast"
    ffmpeg_intermediate_crf: int = 26

    # === Video Settings ===
    video_orientation: Literal["landscape", "portrait", "square", "any"] = "landscape"
    transition_type: str = "crossfade"
    transition_duration: float = 0.5

    # === Audio ===
    enable_background_music: bool = True
    music_mood: Optional[str] = None
    music_volume: float = 0.15

    # === Subtitles ===
    enable_subtitles: bool = True
    subtitle_style: str = "youtube"
    subtitle_offset: float = 0.15

    # === Effects ===
    enable_color_grading: bool = True
    color_preset: str = "balanced"
    custom_lut_file: str = ""
    enable_subscribe_overlay: bool = False
    subscribe_video_path: str = "subscribe.mp4"
    subscribe_scale: float = 0.0
    subscribe_margin_bottom: int = 50
    subscribe_chromakey_color: str = "0x00FF00"
    subscribe_chromakey_similarity: float = 0.3
    subscribe_chromakey_blend: float = 0.15

    # === Loop/Outro ===
    enable_video_loop: bool = False
    video_outro_duration: float = 1.0
    fade_out_duration: float = 1.0

    # === ASR ===
    whisper_model: str = "tiny"

    # === API ===
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # === Redis ===
    redis_url: str = "redis://localhost:6379"
    session_ttl_hours: int = 24

    # === Database ===
    database_url: str = "postgresql+psycopg://aivideo:aivideo_dev@localhost:5432/aivideo"

    # === Cleanup ===
    temp_retention_hours: int = 48

    # === CORS ===
    cors_origins: str = "http://localhost:3000"

    # === Environment ===
    # "production" deletes local video copies after a successful R2 upload.
    environment: Literal["development", "production"] = "development"

    # === Social Publishing ===
    social_token_encryption_key: str = ""  # Fernet key for OAuth tokens; auto-generated in cache_dir if empty
    social_enabled_providers: str = ""     # Comma-separated: youtube,tiktok,instagram

    # YouTube OAuth (for publishing)
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://localhost:8000/api/publish/oauth/youtube/callback"

    # === Cloud Storage (Cloudflare R2) ===
    use_r2_storage: bool = False
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_signed_url_expiry_seconds: int = 3600

    @model_validator(mode="after")
    def _normalize_database_url(self):
        """Pin plain postgres URLs to the psycopg (v3) driver we install.

        SQLAlchemy 2.0 maps ``postgresql://`` to psycopg2 and 2.1 maps it to
        psycopg, so an unpinned URL breaks depending on the installed version.
        """
        for prefix in ("postgresql://", "postgres://"):
            if self.database_url.startswith(prefix):
                self.database_url = "postgresql+psycopg://" + self.database_url[len(prefix):]
                break
        return self

    @model_validator(mode="after")
    def _validate_r2_settings(self):
        """Ensure R2 credentials are present when R2 storage is enabled."""
        if self.use_r2_storage:
            missing = [
                name for name, val in [
                    ("R2_ACCOUNT_ID", self.r2_account_id),
                    ("R2_ACCESS_KEY_ID", self.r2_access_key_id),
                    ("R2_SECRET_ACCESS_KEY", self.r2_secret_access_key),
                    ("R2_BUCKET_NAME", self.r2_bucket_name),
                ] if not val
            ]
            if missing:
                raise ValueError(
                    f"USE_R2_STORAGE is enabled but required credentials are missing: {', '.join(missing)}"
                )
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars


# Global settings instance
settings = Settings()


# === Video Resolution Constants ===
# Used by video_assembler and effects_engine

LANDSCAPE_RESOLUTION = (1920, 1080)
PORTRAIT_RESOLUTION = (1080, 1920)
SQUARE_RESOLUTION = (1080, 1080)

RESOLUTIONS = {
    "landscape": LANDSCAPE_RESOLUTION,
    "portrait": PORTRAIT_RESOLUTION,
    "square": SQUARE_RESOLUTION,
}


def get_resolution(orientation: str) -> tuple:
    """Get resolution tuple for given orientation."""
    return RESOLUTIONS.get(orientation, LANDSCAPE_RESOLUTION)


def get_words_per_second(provider: str = None) -> float:
    """Calculate words per second based on TTS engine and settings.

    Args:
        provider: Optional TTS provider name to use for calculation.
                  If None, falls back to legacy tts_engine setting.
    """
    if settings.words_per_second:
        return settings.words_per_second

    engine = provider or settings.tts_default_provider or settings.tts_engine

    if engine == "kokoro":
        return 3.0 * settings.kokoro_speed
    elif engine == "chatterbox":
        return 2.5
    elif engine == "fish":
        return settings.fish_audio_words_per_second
    else:
        return 3.0
