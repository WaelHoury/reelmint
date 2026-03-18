"""Chatterbox TTS provider — wraps the existing TTSGenerator for Chatterbox."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from .base import TTSProvider, TTSResult, VoiceCatalogEntry


class ChatterboxProvider(TTSProvider):
    name = "chatterbox"

    def __init__(self):
        self._generator = None

    def _get_generator(self):
        if self._generator is None:
            from tts_generator import TTSGenerator

            self._generator = TTSGenerator(
                tts_engine="chatterbox",
                chatterbox_exaggeration=settings.chatterbox_exaggeration,
                chatterbox_cfg=settings.chatterbox_cfg,
                chatterbox_voice_ref=settings.chatterbox_voice_ref,
                ffmpeg_path=settings.ffmpeg_path,
            )
        return self._generator

    def generate(self, text: str, voice_id: str, output_dir: Path) -> TTSResult:
        gen = self._get_generator()
        output_path = output_dir / "narration_raw.wav"
        raw = gen._generate_raw(text, output_path)
        return TTSResult(audio_path=raw, sample_rate=24000)

    def generate_with_timestamps(
        self, text: str, voice_id: str, output_dir: Path
    ) -> TTSResult:
        gen = self._get_generator()
        output_path = output_dir / "narration_raw.wav"
        raw_path, timestamps = gen.generate_with_timestamps(text, output_path)
        return TTSResult(audio_path=raw_path, sample_rate=24000, word_timestamps=timestamps)

    def list_voices(self, user_id: Optional[int] = None) -> List[VoiceCatalogEntry]:
        return [
            VoiceCatalogEntry(
                voice_key="chatterbox:default",
                provider="chatterbox",
                voice_id="default",
                label="Chatterbox Default",
                gender=None,
                grade="CUDA required",
                tags=["clone-capable"],
            )
        ]
