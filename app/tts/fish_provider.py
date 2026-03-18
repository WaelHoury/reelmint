"""Fish Audio TTS provider — uses the official fish-audio-sdk (fishaudio package)."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any, List, Optional
from urllib.request import urlopen

from app.core.config import settings
from .base import TTSProvider, TTSResult, VoiceCatalogEntry

logger = logging.getLogger(__name__)

DEFAULT_FISH_VOICE_ID = "__default__"
CURATED_FISH_VOICES = [
    {
        "voice_id": "b347db033a6549378b48d00acb0d06cd",
        "label": "Selene",
        "description": "A meditative female voice.",
        "tags": ["official", "default"],
    },
    {
        "voice_id": "bf322df2096a46f18c579d0baa36f41d",
        "label": "Adrian",
        "description": "A steady and reliable narrator.",
        "tags": ["official", "default"],
    },
    {
        "voice_id": "933563129e564b19a115bedd57b7406a",
        "label": "Sarah",
        "description": "An engaged speaker.",
        "tags": ["official", "default"],
    },
    {
        "voice_id": "536d3a5e000945adb7038665781a4aca",
        "label": "Ethan",
        "description": "A curious explainer.",
        "tags": ["official", "default"],
    },
    {
        "voice_id": "e3cd384158934cc9a01029cd7d278634",
        "label": "Laura",
        "description": "A confident female narrator.",
        "tags": ["official", "default"],
    },
    {
        "voice_id": "79d0bd3e4e5444b18f7b6d89b5927bf1",
        "label": "Jordan",
        "description": "A motivational speaker.",
        "tags": ["official", "default"],
    },
    {
        "voice_id": "9a9cf47702da476aa4629e2506d4a857",
        "label": "Hannah",
        "description": "A conversation specialist.",
        "tags": ["official", "starter"],
    },
]


class FishAudioProvider(TTSProvider):
    name = "fish"

    def __init__(self):
        self._client = None
        self._public_voice_cache: List[VoiceCatalogEntry] = []
        self._public_voice_cache_expires_at = 0.0

    def _get_client(self):
        if self._client is None:
            try:
                from fishaudio import FishAudio
            except ImportError:
                raise ImportError(
                    "fish-audio-sdk not installed. Install with: pip install fish-audio-sdk"
                )
            if not settings.fish_audio_api_key:
                raise ValueError("FISH_AUDIO_API_KEY is required for Fish Audio TTS")
            self._client = FishAudio(api_key=settings.fish_audio_api_key)
        return self._client

    # ---- generation ----

    def generate(self, text: str, voice_id: str, output_dir: Path) -> TTSResult:
        return self._synthesize(text, voice_id, output_dir)

    def generate_with_timestamps(
        self, text: str, voice_id: str, output_dir: Path
    ) -> TTSResult:
        # Fish Audio doesn't return word timestamps — we return None and let
        # the caller fall back to ASR (faster-whisper) which keeps transcript
        # computation local and avoids extra Fish API costs.
        return self._synthesize(text, voice_id, output_dir)

    def _synthesize(self, text: str, voice_id: str, output_dir: Path) -> TTSResult:
        """Call Fish Audio TTS API and write result to disk.

        Uses client.tts.convert() which returns audio bytes directly.
        """
        client = self._get_client()
        output_path = output_dir / "narration_raw.mp3"

        # Generate audio via Fish Audio API
        request_kwargs = dict(
            text=text,
            model=settings.fish_audio_model,
            format="mp3",
        )
        if voice_id != DEFAULT_FISH_VOICE_ID:
            request_kwargs["reference_id"] = voice_id

        audio_bytes = client.tts.convert(**request_kwargs)

        # Write to disk
        with open(output_path, "wb") as f:
            if isinstance(audio_bytes, bytes):
                f.write(audio_bytes)
            else:
                # Handle streaming response (iterator of chunks)
                for chunk in audio_bytes:
                    f.write(chunk)

        # Convert MP3 → WAV (24kHz mono PCM) for pipeline compatibility
        wav_path = output_dir / "narration_raw.wav"
        cmd = [
            settings.ffmpeg_path,
            "-i", str(output_path),
            "-ar", "24000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            "-y",
            str(wav_path),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)

        # Clean up mp3
        output_path.unlink(missing_ok=True)

        return TTSResult(audio_path=wav_path, sample_rate=24000)

    def generate_preview(self, voice_id: str, text: str, output_dir: Path) -> Path:
        """Use Fish's built-in sample clip when available.

        Preview requests must never fall back to paid TTS generation. If no
        sample clip exists for the requested voice, signal that preview is
        unavailable so the API can return a user-facing message instead.
        """
        sample_path = self._download_preview_sample(voice_id, output_dir)
        if sample_path is not None:
            return sample_path
        raise FileNotFoundError("Preview sample not available for this Fish voice")

    # ---- catalog ----

    def list_voices(self, user_id: Optional[int] = None) -> List[VoiceCatalogEntry]:
        """Return official Fish starter voices, optional public voices, and user clones."""
        voices: List[VoiceCatalogEntry] = []

        # --- Curated Fish starter voices shown by default in the app ---
        voices.extend(self._get_official_default_voices())

        # --- Curated presets (optional admin-pinned voices) ---
        preset_ids = [
            v.strip()
            for v in settings.fish_preset_voice_ids.split(",")
            if v.strip()
        ]
        if preset_ids:
            voices.extend(self._fetch_preset_voices(preset_ids))

        # --- Public Fish voices (discoverable via Fish SDK) ---
        if settings.fish_public_voices_enabled:
            voices.extend(self._get_public_voices())

        # --- User's private cloned voices (from local DB, not Fish API) ---
        if user_id is not None:
            voices.extend(self._get_user_cloned_voices(user_id))

        return self._dedupe_catalog_entries(voices)

    def _get_official_default_voices(self) -> List[VoiceCatalogEntry]:
        """Return the curated Fish starter voices shown in the app."""
        return [
            VoiceCatalogEntry(
                voice_key=f"fish:{voice['voice_id']}",
                provider="fish",
                voice_id=voice["voice_id"],
                label=voice["label"],
                description=voice["description"],
                tags=list(voice["tags"]),
            )
            for voice in CURATED_FISH_VOICES
        ]

    def _fetch_preset_voices(self, voice_ids: List[str]) -> List[VoiceCatalogEntry]:
        """Fetch metadata for admin-curated Fish voices."""
        entries: List[VoiceCatalogEntry] = []
        try:
            client = self._get_client()
            for vid in voice_ids:
                try:
                    voice = client.voices.get(vid)
                    entry = self._build_catalog_entry(
                        voice,
                        voice_id_override=vid,
                        extra_tags=["preset"],
                        allow_private=True,
                    )
                    if entry:
                        entries.append(entry)
                except Exception as e:
                    logger.warning("Failed to fetch Fish preset voice %s: %s", vid, e)
        except Exception as e:
            logger.warning("Cannot connect to Fish Audio API for presets: %s", e)
        return entries

    def _get_public_voices(self) -> List[VoiceCatalogEntry]:
        """Load public Fish voices with a small in-memory cache."""
        now = time.time()
        cache_ttl = max(settings.fish_public_voice_cache_seconds, 0)
        if self._public_voice_cache and now < self._public_voice_cache_expires_at:
            return list(self._public_voice_cache)

        try:
            response = self._list_public_voices_from_sdk()
            entries = []
            for item in self._extract_voice_items(response):
                entry = self._build_catalog_entry(item, extra_tags=["public"])
                if entry:
                    entries.append(entry)
            self._public_voice_cache = self._dedupe_catalog_entries(entries)
            self._public_voice_cache_expires_at = now + cache_ttl if cache_ttl else 0.0
        except Exception as e:
            logger.warning("Cannot load Fish public voices: %s", e)

        return list(self._public_voice_cache)

    def _download_preview_sample(self, voice_id: str, output_dir: Path) -> Optional[Path]:
        """Download the first sample clip from Fish voice metadata for reliable previews."""
        if voice_id == DEFAULT_FISH_VOICE_ID:
            return None

        try:
            voice = self._get_client().voices.get(voice_id)
        except Exception as e:
            logger.warning("Cannot load Fish voice metadata for preview %s: %s", voice_id, e)
            return None

        sample_url = self._extract_preview_sample_url(voice)
        if not sample_url:
            return None

        sample_path = output_dir / f"preview_source_{voice_id}.mp3"
        try:
            with urlopen(sample_url) as response:
                sample_path.write_bytes(response.read())
            return sample_path
        except Exception as e:
            logger.warning("Cannot download Fish preview sample for %s: %s", voice_id, e)
            sample_path.unlink(missing_ok=True)
            return None

    def _extract_preview_sample_url(self, voice: Any) -> Optional[str]:
        samples = self._get_value(voice, "samples", default=[]) or []
        if not isinstance(samples, list):
            return None

        for sample in samples:
            for key in ("audio", "audio_url", "url"):
                value = self._get_value(sample, key, default=None)
                if value:
                    return str(value).strip()
        return None

    def _list_public_voices_from_sdk(self):
        """Call Fish SDK voice discovery with graceful fallback for older SDK versions."""
        client = self._get_client()
        base_kwargs = {
            "page_size": max(settings.fish_public_voice_page_size, 1),
            "page_number": max(settings.fish_public_voice_page_number, 1),
        }
        advanced_kwargs = {
            **base_kwargs,
            "sort_by": settings.fish_public_voice_sort_by,
            "type": "tts",
        }

        try:
            return client.voices.list(**advanced_kwargs)
        except TypeError:
            # Older SDK builds may not support sort_by/type kwargs yet.
            return client.voices.list(**base_kwargs)

    def _extract_voice_items(self, response: Any) -> List[Any]:
        """Normalize paginated Fish SDK responses into a plain list of items."""
        if response is None:
            return []
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            items = response.get("items") or response.get("voices") or response.get("results")
            return list(items or [])

        for attr_name in ("items", "voices", "results", "data"):
            value = getattr(response, attr_name, None)
            if value is None or callable(value):
                continue
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = value.get("items") or value.get("voices") or value.get("results")
                if nested:
                    return list(nested)

        return []

    def _build_catalog_entry(
        self,
        voice: Any,
        *,
        voice_id_override: Optional[str] = None,
        extra_tags: Optional[List[str]] = None,
        allow_private: bool = False,
    ) -> Optional[VoiceCatalogEntry]:
        """Map a Fish voice/model response into the app's catalog format."""
        voice_id = voice_id_override or self._get_value(voice, "_id", "id")
        if not voice_id:
            return None

        voice_type = str(self._get_value(voice, "type", default="tts") or "tts").strip().lower()
        visibility = str(self._get_value(voice, "visibility", default="public") or "public").strip().lower()
        state = str(self._get_value(voice, "state", default="") or "").strip().lower()

        if voice_type and voice_type != "tts":
            return None
        if not allow_private and visibility == "private":
            return None
        if state == "failed":
            return None

        tags = []
        raw_tags = self._get_value(voice, "tags", default=[]) or []
        if isinstance(raw_tags, list):
            tags.extend(str(tag).strip() for tag in raw_tags if str(tag).strip())
        if extra_tags:
            tags.extend(extra_tags)

        language = self._extract_primary_language(voice)
        if language and language != "en":
            tags.append(language)

        return VoiceCatalogEntry(
            voice_key=f"fish:{voice_id}",
            provider="fish",
            voice_id=voice_id,
            label=self._build_voice_label(voice, voice_id),
            description=self._build_voice_description(voice),
            language=language,
            grade=self._build_voice_grade(voice),
            tags=self._dedupe_tags(tags),
        )

    def _build_voice_label(self, voice: Any, voice_id: str) -> str:
        title = str(self._get_value(voice, "title", default="") or "").strip()
        author = self._get_value(voice, "author", default=None)
        author_name = str(
            self._get_value(author, "nickname", "name", default="") or ""
        ).strip()
        if title and author_name:
            return f"{title} - {author_name}"
        if title:
            return title
        return voice_id

    def _build_voice_grade(self, voice: Any) -> Optional[str]:
        task_count = self._get_value(voice, "task_count", default=None)
        if task_count is None:
            return None
        try:
            return f"{int(task_count)} uses"
        except (TypeError, ValueError):
            return None

    def _build_voice_description(self, voice: Any) -> Optional[str]:
        description = str(self._get_value(voice, "description", default="") or "").strip()
        if description:
            return description
        author = self._get_value(voice, "author", default=None)
        author_name = str(
            self._get_value(author, "nickname", "name", default="") or ""
        ).strip()
        if author_name:
            return f"Community Fish voice by {author_name}."
        return "Community Fish voice."

    def _extract_primary_language(self, voice: Any) -> str:
        languages = self._get_value(voice, "languages", default=[]) or []
        if not isinstance(languages, list) or not languages:
            return "en"

        first = languages[0]
        if isinstance(first, str):
            return first.strip().lower() or "en"

        if isinstance(first, dict):
            for key in ("code", "language", "name"):
                value = first.get(key)
                if value:
                    return str(value).strip().lower()

        for key in ("code", "language", "name"):
            value = getattr(first, key, None)
            if value:
                return str(value).strip().lower()

        return "en"

    @staticmethod
    def _get_value(source: Any, *names: str, default: Any = None) -> Any:
        """Read a field from either a dict-like or object-like SDK response."""
        if source is None:
            return default
        for name in names:
            if isinstance(source, dict) and name in source:
                return source[name]
            value = getattr(source, name, None)
            if value is not None:
                return value
        return default

    @staticmethod
    def _dedupe_tags(tags: List[str]) -> List[str]:
        seen = set()
        deduped = []
        for tag in tags:
            normalized = str(tag).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    @staticmethod
    def _dedupe_catalog_entries(entries: List[VoiceCatalogEntry]) -> List[VoiceCatalogEntry]:
        seen = set()
        deduped: List[VoiceCatalogEntry] = []
        for entry in entries:
            if entry.voice_key in seen:
                continue
            seen.add(entry.voice_key)
            deduped.append(entry)
        return deduped

    def _get_user_cloned_voices(self, user_id: int) -> List[VoiceCatalogEntry]:
        """Load user's cloned voices from local database."""
        try:
            from app.db.base import SessionLocal
            from app.db.models import UserTTSVoice

            db = SessionLocal()
            try:
                voices = (
                    db.query(UserTTSVoice)
                    .filter(
                        UserTTSVoice.user_id == user_id,
                        UserTTSVoice.status == "ready",
                    )
                    .all()
                )
                return [
                    VoiceCatalogEntry(
                        voice_key=f"fish:{v.fish_voice_id}",
                        provider="fish",
                        voice_id=v.fish_voice_id,
                        label=v.title,
                        is_cloned=True,
                        owner_user_id=user_id,
                        tags=["cloned", "private"],
                    )
                    for v in voices
                ]
            finally:
                db.close()
        except Exception as e:
            logger.warning("Failed to load cloned voices for user %d: %s", user_id, e)
            return []

    # ---- voice cloning ----

    def create_clone(
        self,
        title: str,
        audio_path: Path,
        user_id: int,
        transcript: Optional[str] = None,
    ) -> dict:
        """Create a private cloned voice on Fish Audio.

        Returns dict with fish_voice_id, title, status.
        """
        client = self._get_client()

        # Read audio file
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        # Create voice model — voices param is a list of audio bytes
        create_kwargs = {
            "title": title,
            "voices": [audio_data],
            "description": f"Voice clone for user {user_id}",
            "visibility": "private",
            "type": "tts",
            "enhance_audio_quality": True,
        }
        if transcript:
            create_kwargs["texts"] = [transcript]

        voice = client.voices.create(**create_kwargs)

        voice_id = voice.id if hasattr(voice, "id") else str(voice)
        initial_status = self._map_voice_state(getattr(voice, "state", None))

        return {
            "fish_voice_id": voice_id,
            "title": title,
            "status": initial_status,
        }

    def get_voice_status(self, fish_voice_id: str) -> str:
        """Check if a cloned voice is ready."""
        try:
            client = self._get_client()
            voice = client.voices.get(fish_voice_id)
            return self._map_voice_state(getattr(voice, "state", None))
        except Exception:
            return "processing"

    def delete_voice(self, fish_voice_id: str) -> bool:
        """Delete a voice from Fish Audio."""
        try:
            client = self._get_client()
            client.voices.delete(fish_voice_id)
            return True
        except Exception as e:
            logger.error("Failed to delete Fish voice %s: %s", fish_voice_id, e)
            return False

    @staticmethod
    def _map_voice_state(state: object) -> str:
        """Map Fish SDK voice states into the app's smaller status set."""
        normalized = str(state or "").strip().lower()
        if normalized in {"ready", "trained"}:
            return "ready"
        if normalized == "failed":
            return "failed"
        return "processing"
