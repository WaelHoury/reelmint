"""Centralized configuration and settings for AI Video Maker."""

import os
from pathlib import Path
from typing import Literal, Optional
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
    cache_dir: Path = Path("cache")
    music_dir: Path = Path("music")
    models_dir: Path = Path("models")

    # === LLM Provider ===
    llm_provider: Literal["ollama", "openrouter"] = "ollama"
    narration_style: str = "viral"

    # Ollama
    ollama_model: str = "qwen2.5:3b"
    ollama_base_url: str = "http://localhost:11434"

    # OpenRouter
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "google/gemini-2.0-flash-001"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # === TTS ===
    tts_engine: Literal["kokoro", "piper", "orpheus", "chatterbox"] = "kokoro"
    words_per_second: Optional[float] = None  # Override, or calculated from engine

    # Kokoro
    kokoro_voice: str = "af_heart"
    kokoro_speed: float = 1.0
    kokoro_lang: str = "a"

    # Piper
    piper_executable: str = "piper.exe"
    piper_model_path: Optional[str] = None

    # Orpheus
    orpheus_voice: str = "tara"

    # Chatterbox
    chatterbox_exaggeration: float = 0.5
    chatterbox_cfg: float = 0.5
    chatterbox_voice_ref: str = ""

    # === Video Search (Pexels) ===
    pexels_api_key: str = ""

    # === CLIP Matching ===
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    clip_candidates: int = 5

    # === FFmpeg ===
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

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
    whisper_model: str = "base"

    # === API ===
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # === Redis ===
    redis_url: str = "redis://localhost:6379"
    session_ttl_hours: int = 24

    # === Database ===
    database_url: str = "postgresql://aivideo:aivideo_dev@localhost:5432/aivideo"

    # === Auth ===
    google_client_id: str = ""
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 72

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


def get_words_per_second() -> float:
    """Calculate words per second based on TTS engine and settings."""
    if settings.words_per_second:
        return settings.words_per_second

    if settings.tts_engine == "kokoro":
        return 3.0 * settings.kokoro_speed
    elif settings.tts_engine == "chatterbox":
        return 2.5
    else:
        return 3.0
