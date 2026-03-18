"""Kokoro TTS provider — wraps the existing TTSGenerator for Kokoro voices."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from .base import TTSProvider, TTSResult, VoiceCatalogEntry


class KokoroProvider(TTSProvider):
    name = "kokoro"

    def __init__(self):
        self._generator = None  # Lazy

    def _get_generator(self):
        if self._generator is None:
            from tts_generator import TTSGenerator

            self._generator = TTSGenerator(
                tts_engine="kokoro",
                kokoro_voice=settings.kokoro_voice,
                kokoro_speed=settings.kokoro_speed,
                kokoro_lang=settings.kokoro_lang,
                ffmpeg_path=settings.ffmpeg_path,
            )
        return self._generator

    # ---- generation ----

    def generate(self, text: str, voice_id: str, output_dir: Path) -> TTSResult:
        gen = self._get_generator()
        gen.kokoro_voice = voice_id
        output_path = output_dir / "narration_raw.wav"
        raw = gen._generate_raw(text, output_path)
        return TTSResult(audio_path=raw, sample_rate=24000)

    def generate_with_timestamps(
        self, text: str, voice_id: str, output_dir: Path
    ) -> TTSResult:
        gen = self._get_generator()
        gen.kokoro_voice = voice_id
        output_path = output_dir / "narration_raw.wav"
        raw_path, timestamps = gen.generate_with_timestamps(text, output_path)
        return TTSResult(audio_path=raw_path, sample_rate=24000, word_timestamps=timestamps)

    # ---- catalog ----

    def list_voices(self, user_id: Optional[int] = None) -> List[VoiceCatalogEntry]:
        from tts_generator import TTSGenerator

        voices: List[VoiceCatalogEntry] = []
        raw = TTSGenerator.list_kokoro_voices()
        for lang_name, genders in raw.items():
            for gender, voice_list in genders.items():
                for voice_id, grade_desc in voice_list:
                    voices.append(
                        VoiceCatalogEntry(
                            voice_key=f"kokoro:{voice_id}",
                            provider="kokoro",
                            voice_id=voice_id,
                            label=f"{voice_id} ({lang_name})",
                            language=_lang_code(lang_name),
                            gender=gender,
                            grade=grade_desc,
                            tags=[lang_name.lower()],
                        )
                    )
        return voices


def _lang_code(lang_name: str) -> str:
    mapping = {
        "American English": "en",
        "British English": "en",
        "French": "fr",
        "Japanese": "ja",
        "Hindi": "hi",
        "Italian": "it",
    }
    return mapping.get(lang_name, "en")
