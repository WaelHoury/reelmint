"""TTS provider registry — unified interface for multiple TTS backends."""

from .registry import TTSProviderRegistry, get_tts_registry
from .base import TTSProvider, TTSResult, VoiceCatalogEntry

__all__ = [
    "TTSProviderRegistry",
    "get_tts_registry",
    "TTSProvider",
    "TTSResult",
    "VoiceCatalogEntry",
]
