"""TTS provider registry — singleton that manages available providers."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from .base import TTSProvider, TTSResult, VoiceCatalogEntry

logger = logging.getLogger(__name__)

_registry_instance: Optional[TTSProviderRegistry] = None


def _dedupe_provider_names(names: List[str]) -> List[str]:
    """Preserve provider order while removing duplicates and blanks."""
    seen = set()
    deduped: List[str] = []
    for name in names:
        normalized = (name or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def get_configured_provider_names() -> List[str]:
    """Return the active provider list from explicit config or legacy fallback."""
    explicit = _dedupe_provider_names(
        os.getenv("TTS_ENABLED_PROVIDERS", "").split(",")
    )
    if explicit:
        names = explicit
    else:
        legacy = (settings.tts_engine or "").strip().lower()
        names = [legacy] if legacy else []

    # A Fish Audio key is enough to unlock Fish voices; no need to also list it.
    if (settings.fish_audio_api_key or "").strip() and "fish" not in names:
        names = names + ["fish"]
    return names


class TTSProviderRegistry:
    """Central registry for all enabled TTS providers."""

    def __init__(self):
        self._providers: Dict[str, TTSProvider] = {}
        self._load_enabled_providers()

    def _load_enabled_providers(self):
        enabled = get_configured_provider_names()

        for name in enabled:
            try:
                provider = self._create_provider(name)
                if provider:
                    self._providers[name] = provider
                    logger.info("TTS provider registered: %s", name)
            except Exception as e:
                logger.warning("Failed to register TTS provider '%s': %s", name, e)

    @staticmethod
    def _create_provider(name: str) -> Optional[TTSProvider]:
        if name == "kokoro":
            from .kokoro_provider import KokoroProvider
            return KokoroProvider()
        elif name == "chatterbox":
            from .chatterbox_provider import ChatterboxProvider
            return ChatterboxProvider()
        elif name == "fish":
            if not settings.fish_audio_api_key:
                logger.warning("Fish Audio API key not set — provider disabled")
                return None
            from .fish_provider import FishAudioProvider
            return FishAudioProvider()
        else:
            logger.warning("Unknown TTS provider: %s", name)
            return None

    # ---- public API ----

    def get_provider(self, name: str) -> TTSProvider:
        """Get a provider by name. Raises KeyError if not registered."""
        if name not in self._providers:
            raise KeyError(
                f"TTS provider '{name}' is not enabled. "
                f"Available: {list(self._providers.keys())}"
            )
        return self._providers[name]

    @property
    def enabled_providers(self) -> List[str]:
        return list(self._providers.keys())

    def get_catalog(self, user_id: Optional[int] = None) -> List[VoiceCatalogEntry]:
        """Aggregate voice catalog from all enabled providers."""
        catalog: List[VoiceCatalogEntry] = []
        for provider in self._providers.values():
            try:
                catalog.extend(provider.list_voices(user_id=user_id))
            except Exception as e:
                logger.warning("Failed to load catalog from %s: %s", provider.name, e)
        return catalog

    def generate(
        self,
        text: str,
        provider_name: str,
        voice_id: str,
        output_dir: Path,
        with_timestamps: bool = False,
    ) -> TTSResult:
        """Generate TTS audio using the specified provider and voice.

        Args:
            text: Narration text
            provider_name: Provider key ("kokoro", "fish", etc.)
            voice_id: Provider-local voice ID
            output_dir: Directory to write output files
            with_timestamps: If True, request word-level timestamps
        """
        provider = self.get_provider(provider_name)
        if with_timestamps:
            return provider.generate_with_timestamps(text, voice_id, output_dir)
        return provider.generate(text, voice_id, output_dir)

    def generate_from_voice_key(
        self,
        text: str,
        voice_key: str,
        output_dir: Path,
        with_timestamps: bool = False,
    ) -> TTSResult:
        """Generate TTS using a composite voice_key like 'kokoro:af_heart'."""
        provider_name, voice_id = parse_voice_key(voice_key)
        return self.generate(text, provider_name, voice_id, output_dir, with_timestamps)

    def generate_preview(
        self, voice_key: str, text: str, output_dir: Path
    ) -> Path:
        """Generate a short preview audio clip."""
        provider_name, voice_id = parse_voice_key(voice_key)
        provider = self.get_provider(provider_name)
        return provider.generate_preview(voice_id, text, output_dir)


def parse_voice_key(voice_key: str) -> tuple:
    """Split 'provider:voice_id' → (provider, voice_id)."""
    if ":" not in voice_key:
        raise ValueError(f"Invalid voice_key format '{voice_key}' — expected 'provider:voice_id'")
    provider, voice_id = voice_key.split(":", 1)
    return provider, voice_id


def get_tts_registry() -> TTSProviderRegistry:
    """Get or create the global registry singleton."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = TTSProviderRegistry()
    return _registry_instance
