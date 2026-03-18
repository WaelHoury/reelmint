"""Base class and data types for TTS providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class VoiceCatalogEntry:
    """A single voice exposed to the frontend catalog."""

    voice_key: str  # Globally unique: "provider:voice_id"
    provider: str  # "kokoro", "chatterbox", "fish"
    voice_id: str  # Provider-local ID (e.g. "af_heart", Fish UUID)
    label: str  # Human-readable display name
    description: Optional[str] = None
    language: str = "en"
    gender: Optional[str] = None  # "female", "male", or None
    grade: Optional[str] = None  # Quality tier hint
    is_cloned: bool = False  # True for user-created Fish clones
    owner_user_id: Optional[int] = None  # Set for private cloned voices
    tags: List[str] = field(default_factory=list)


@dataclass
class TTSResult:
    """Output of a TTS generation call."""

    audio_path: Path  # Path to raw (unnormalized) audio file
    sample_rate: int = 24000
    word_timestamps: Optional[List[Dict]] = None  # [{word, start, end}]


class TTSProvider(ABC):
    """Abstract base for a TTS provider."""

    name: str  # e.g. "kokoro", "fish"

    @abstractmethod
    def generate(self, text: str, voice_id: str, output_dir: Path) -> TTSResult:
        """Synthesise *text* with the given voice, writing audio into *output_dir*.

        Returns a TTSResult whose audio_path points to raw (pre-normalization) WAV.
        """

    @abstractmethod
    def generate_with_timestamps(
        self, text: str, voice_id: str, output_dir: Path
    ) -> TTSResult:
        """Like generate() but also returns word-level timestamps when possible."""

    @abstractmethod
    def list_voices(self, user_id: Optional[int] = None) -> List[VoiceCatalogEntry]:
        """Return the voice catalog for this provider.

        If user_id is supplied, include that user's private cloned voices.
        """

    def generate_preview(self, voice_id: str, text: str, output_dir: Path) -> Path:
        """Generate a short preview clip. Default delegates to generate()."""
        result = self.generate(text, voice_id, output_dir)
        return result.audio_path
