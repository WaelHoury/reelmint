"""Shared TTS validation helpers for API routes."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import UserTTSVoice
from app.tts import get_tts_registry
from app.tts.base import VoiceCatalogEntry
from app.tts.registry import parse_voice_key


def resolve_tts_selection_for_user(
    *,
    provider: str | None,
    voice_key: str,
    user_id: int,
) -> VoiceCatalogEntry:
    """Validate that *voice_key* is available to *user_id* and matches *provider*.

    Returns the canonical catalog entry so routes can persist trusted provider/label
    values instead of reusing arbitrary client input.
    """
    if not voice_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="voice_key is required",
        )

    try:
        resolved_provider, voice_id = parse_voice_key(voice_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if provider and provider != resolved_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provider must match the provider encoded in voice_key",
        )

    registry = get_tts_registry()
    if resolved_provider not in registry.enabled_providers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"TTS provider '{resolved_provider}' is not enabled. "
                f"Available: {registry.enabled_providers}"
            ),
        )

    if resolved_provider == "fish":
        return _resolve_fish_voice_for_user(voice_id, user_id)

    provider_impl = registry.get_provider(resolved_provider)
    for entry in provider_impl.list_voices(user_id=user_id):
        if entry.voice_key == voice_key:
            return entry

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown or unavailable voice_key '{voice_key}'",
    )


def resolve_default_tts_selection_for_user(
    *,
    user_id: int,
    catalog: list[VoiceCatalogEntry] | None = None,
) -> VoiceCatalogEntry | None:
    """Pick the best default voice for a user from the currently available catalog."""
    available = catalog if catalog is not None else get_tts_registry().get_catalog(user_id=user_id)
    if not available:
        return None

    preferred_voice_key = (settings.tts_default_voice_key or "").strip()
    if preferred_voice_key:
        for entry in available:
            if entry.voice_key == preferred_voice_key:
                return entry

    preferred_provider = (settings.tts_default_provider or "").strip().lower()
    if preferred_provider:
        for entry in available:
            if entry.provider == preferred_provider:
                return entry

    return available[0]


def _resolve_fish_voice_for_user(voice_id: str, user_id: int) -> VoiceCatalogEntry:
    """Validate access to a Fish voice without trusting client-provided metadata."""
    registry = get_tts_registry()
    try:
        provider_impl = registry.get_provider("fish")
        for entry in provider_impl.list_voices(user_id=user_id):
            if entry.voice_id == voice_id:
                return entry
    except Exception:
        # Fall through to local checks so preset/config validation still works.
        pass

    preset_ids = {v.strip() for v in settings.fish_preset_voice_ids.split(",") if v.strip()}
    if voice_id in preset_ids:
        return VoiceCatalogEntry(
            voice_key=f"fish:{voice_id}",
            provider="fish",
            voice_id=voice_id,
            label=voice_id,
            tags=["preset"],
        )

    db = SessionLocal()
    try:
        voice = (
            db.query(UserTTSVoice)
            .filter(UserTTSVoice.fish_voice_id == voice_id)
            .first()
        )
        if not voice or voice.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Voice not found",
            )
        if voice.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Voice is not ready yet",
            )

        return VoiceCatalogEntry(
            voice_key=f"fish:{voice.fish_voice_id}",
            provider="fish",
            voice_id=voice.fish_voice_id,
            label=voice.title,
            is_cloned=True,
            owner_user_id=user_id,
            tags=["cloned", "private"],
        )
    finally:
        db.close()
