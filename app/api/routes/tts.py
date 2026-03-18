"""TTS voice catalog, preview, Fish Audio voice CRUD, and session TTS selection routes."""

import logging
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    get_current_user_from_header_or_token,
    get_owned_session,
)
from app.api.schemas.tts import (
    FishVoiceListResponse,
    FishVoiceResponse,
    UpdateTTSRequest,
    VoiceCatalogItem,
    VoiceCatalogResponse,
)
from app.api.services.tts_service import (
    resolve_default_tts_selection_for_user,
    resolve_tts_selection_for_user,
)
from app.core.config import settings
from app.db.base import get_db
from app.db.models import User, UserTTSVoice
from app.api.services.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])


# ---------------------------------------------------------------------------
# GET /api/tts/catalog — aggregated voice catalog from all enabled providers
# ---------------------------------------------------------------------------

@router.get("/catalog", response_model=VoiceCatalogResponse)
async def get_voice_catalog(user: User = Depends(get_current_user)):
    """Return the full voice catalog for the current user."""
    from app.tts import get_tts_registry

    registry = get_tts_registry()
    catalog = registry.get_catalog(user_id=user.id)
    default_voice = resolve_default_tts_selection_for_user(
        user_id=user.id,
        catalog=catalog,
    )

    return VoiceCatalogResponse(
        voices=[
            VoiceCatalogItem(
                voice_key=v.voice_key,
                provider=v.provider,
                voice_id=v.voice_id,
                label=v.label,
                description=v.description,
                language=v.language,
                gender=v.gender,
                grade=v.grade,
                is_cloned=v.is_cloned,
                tags=v.tags,
            )
            for v in catalog
        ],
        enabled_providers=registry.enabled_providers,
        default_voice_key=default_voice.voice_key if default_voice else "",
    )


# ---------------------------------------------------------------------------
# GET /api/tts/preview?voice_key=... — generate or serve cached preview audio
# ---------------------------------------------------------------------------

@router.get("/preview")
async def get_voice_preview(
    voice_key: str,
    user: User = Depends(get_current_user_from_header_or_token),
):
    """Generate a short preview audio clip for a voice."""
    from app.tts import get_tts_registry

    validated_tts = resolve_tts_selection_for_user(
        provider=None,
        voice_key=voice_key,
        user_id=user.id,
    )

    cache_dir = _preview_cache_dir()
    safe_name = _safe_preview_name(voice_key)
    cached_file = cache_dir / f"{safe_name}.wav"

    if not cached_file.exists():
        _maybe_download_clone_preview_from_r2(validated_tts=validated_tts, user_id=user.id, cached_file=cached_file)

    if not cached_file.exists():
        registry = get_tts_registry()
        try:
            raw_path = registry.generate_preview(
                voice_key, settings.tts_preview_text, cache_dir
            )
            # Normalize to standard WAV
            subprocess.run(
                [
                    settings.ffmpeg_path, "-i", str(raw_path),
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                    "-y", str(cached_file),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            )
            if raw_path != cached_file and raw_path.exists():
                raw_path.unlink(missing_ok=True)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Preview unavailable for this voice")
        except Exception as e:
            logger.error("Preview generation failed for %s: %s", voice_key, e)
            raise HTTPException(status_code=500, detail="Preview generation failed")

    return FileResponse(
        cached_file,
        media_type="audio/wav",
        filename=f"preview_{safe_name}.wav",
    )


def update_session_tts_handler(session_id: str, request: UpdateTTSRequest, user: User):
    """Shared handler for PUT /api/sessions/{session_id}/tts.

    Called from the sessions router to keep the URL under /api/sessions/.
    """
    session = get_owned_session(session_id, user)

    validated_tts = resolve_tts_selection_for_user(
        provider=request.provider,
        voice_key=request.voice_key,
        user_id=user.id,
    )

    session.tts_provider = validated_tts.provider
    session.tts_voice_key = validated_tts.voice_key
    session.tts_voice_label = validated_tts.label

    # Recalculate estimated duration with new provider's WPS
    if session.script_text:
        from app.core.config import get_words_per_second

        wps = get_words_per_second(provider=validated_tts.provider)
        session.estimated_duration_seconds = len(session.script_text.split()) / wps

    session_store.update(session)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Fish Audio voice CRUD
# ---------------------------------------------------------------------------

@router.get("/fish/voices", response_model=FishVoiceListResponse)
async def list_fish_voices(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's Fish Audio cloned voices."""
    voices = (
        db.query(UserTTSVoice)
        .filter(UserTTSVoice.user_id == user.id)
        .order_by(UserTTSVoice.created_at.desc())
        .all()
    )
    return FishVoiceListResponse(
        voices=[_voice_to_response(v) for v in voices]
    )


@router.post("/fish/voices", response_model=FishVoiceResponse, status_code=201)
async def create_fish_voice(
    title: str = Form(...),
    transcript: str = Form(default=""),
    audio_file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload an audio sample and create a Fish Audio cloned voice."""
    from app.tts.registry import get_configured_provider_names

    if "fish" not in get_configured_provider_names():
        raise HTTPException(status_code=400, detail="Fish Audio provider is not enabled")

    # Validate audio file
    if not audio_file.content_type or not audio_file.content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="File must be an audio file")

    # Read and validate size (max 50MB)
    content = await audio_file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio file too large (max 50MB)")

    # Save to temp file
    suffix = Path(audio_file.filename).suffix if audio_file.filename else ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # Validate duration (10-30 seconds)
        import subprocess

        result = subprocess.run(
            [settings.ffprobe_path, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(tmp_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            duration = float(result.stdout.strip())
            if duration < 10:
                raise HTTPException(status_code=400, detail="Audio must be at least 10 seconds long")
            if duration > 30:
                raise HTTPException(status_code=400, detail="Audio must be 30 seconds or shorter")

        # Normalize audio for best clone quality (mono, 44.1kHz)
        normalized_path = tmp_path.parent / f"normalized_{tmp_path.name}"
        subprocess.run(
            [settings.ffmpeg_path, "-i", str(tmp_path),
             "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le",
             "-y", str(normalized_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True,
        )

        # Create clone on Fish Audio
        from app.tts.fish_provider import FishAudioProvider

        fish = FishAudioProvider()
        clone_result = fish.create_clone(
            title=title,
            audio_path=normalized_path,
            user_id=user.id,
            transcript=transcript if transcript.strip() else None,
        )

        _save_clone_preview_sample(
            normalized_path=normalized_path,
            fish_voice_id=clone_result["fish_voice_id"],
        )
        _maybe_upload_clone_preview_to_r2(
            user_id=user.id,
            fish_voice_id=clone_result["fish_voice_id"],
        )

        # Save to local database
        voice = UserTTSVoice(
            user_id=user.id,
            fish_voice_id=clone_result["fish_voice_id"],
            title=title,
            status=clone_result["status"],
        )
        db.add(voice)
        db.commit()
        db.refresh(voice)

        return _voice_to_response(voice)

    finally:
        tmp_path.unlink(missing_ok=True)
        normalized = tmp_path.parent / f"normalized_{tmp_path.name}"
        if normalized.exists():
            normalized.unlink(missing_ok=True)


@router.get("/fish/voices/{voice_id}", response_model=FishVoiceResponse)
async def get_fish_voice(
    voice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a specific Fish voice by local DB ID. Also refreshes status from Fish API."""
    voice = db.query(UserTTSVoice).filter(
        UserTTSVoice.id == voice_id,
        UserTTSVoice.user_id == user.id,
    ).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    # Refresh status from Fish if still processing
    if voice.status == "processing":
        from app.tts.fish_provider import FishAudioProvider

        fish = FishAudioProvider()
        new_status = fish.get_voice_status(voice.fish_voice_id)
        if new_status != voice.status:
            voice.status = new_status
            db.commit()
            db.refresh(voice)

    return _voice_to_response(voice)


@router.delete("/fish/voices/{voice_id}", status_code=204)
async def delete_fish_voice(
    voice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a Fish Audio cloned voice (both from Fish API and local DB)."""
    voice = db.query(UserTTSVoice).filter(
        UserTTSVoice.id == voice_id,
        UserTTSVoice.user_id == user.id,
    ).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    # Delete from Fish Audio API
    from app.tts.fish_provider import FishAudioProvider

    fish = FishAudioProvider()
    fish.delete_voice(voice.fish_voice_id)

    # Delete from local DB
    db.delete(voice)
    db.commit()

    # Invalidate cached preview
    cached = _preview_cache_path_for_voice_key(f"fish:{voice.fish_voice_id}")
    cached.unlink(missing_ok=True)
    _maybe_delete_clone_preview_from_r2(user_id=user.id, fish_voice_id=voice.fish_voice_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _voice_to_response(voice: UserTTSVoice) -> FishVoiceResponse:
    return FishVoiceResponse(
        id=voice.id,
        fish_voice_id=voice.fish_voice_id,
        title=voice.title,
        status=voice.status,
        voice_key=f"fish:{voice.fish_voice_id}",
        created_at=voice.created_at.isoformat() if voice.created_at else "",
    )


def _safe_preview_name(voice_key: str) -> str:
    return voice_key.replace(":", "_").replace("/", "_")


def _preview_cache_dir() -> Path:
    cache_dir = Path(settings.cache_dir) / "tts_previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _preview_cache_path_for_voice_key(voice_key: str) -> Path:
    return _preview_cache_dir() / f"{_safe_preview_name(voice_key)}.wav"


def _save_clone_preview_sample(*, normalized_path: Path, fish_voice_id: str) -> None:
    """Persist a short local preview clip for a cloned voice.

    This keeps preview playback free after the voice has been created by
    serving the user's original sample instead of re-calling Fish TTS.
    """
    cached_file = _preview_cache_path_for_voice_key(f"fish:{fish_voice_id}")
    try:
        subprocess.run(
            [
                settings.ffmpeg_path,
                "-i", str(normalized_path),
                "-t", "12",
                "-ar", "48000",
                "-ac", "2",
                "-c:a", "pcm_s16le",
                "-y", str(cached_file),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except Exception as e:
        logger.warning("Failed to save local preview sample for Fish voice %s: %s", fish_voice_id, e)


def _maybe_download_clone_preview_from_r2(*, validated_tts, user_id: int, cached_file: Path) -> None:
    """Download a cloned voice preview from R2 into the local cache when available."""
    if validated_tts.provider != "fish" or not validated_tts.is_cloned or validated_tts.owner_user_id != user_id:
        return
    if not settings.use_r2_storage:
        return

    try:
        from app.api.services.r2_storage import r2_storage, r2_tts_preview_key

        if not r2_storage.enabled:
            return

        r2_key = r2_tts_preview_key(user_id, validated_tts.voice_id)
        if not r2_storage.file_exists(r2_key):
            return

        r2_storage.download_file(r2_key, cached_file)
    except Exception as e:
        logger.warning("Failed to download cloned Fish preview from R2 for %s: %s", validated_tts.voice_id, e)


def _maybe_upload_clone_preview_to_r2(*, user_id: int, fish_voice_id: str) -> None:
    """Upload a locally saved cloned voice preview to R2 for cross-instance reuse."""
    if not settings.use_r2_storage:
        return

    cached_file = _preview_cache_path_for_voice_key(f"fish:{fish_voice_id}")
    if not cached_file.exists():
        return

    try:
        from app.api.services.r2_storage import r2_storage, r2_tts_preview_key

        if not r2_storage.enabled:
            return

        r2_storage.upload_file(cached_file, r2_tts_preview_key(user_id, fish_voice_id))
    except Exception as e:
        logger.warning("Failed to upload cloned Fish preview to R2 for %s: %s", fish_voice_id, e)


def _maybe_delete_clone_preview_from_r2(*, user_id: int, fish_voice_id: str) -> None:
    """Delete a cloned voice preview from R2 when the voice is removed."""
    if not settings.use_r2_storage:
        return

    try:
        from app.api.services.r2_storage import r2_storage, r2_tts_preview_key

        if not r2_storage.enabled:
            return

        r2_storage.delete_file(r2_tts_preview_key(user_id, fish_voice_id))
    except Exception as e:
        logger.warning("Failed to delete cloned Fish preview from R2 for %s: %s", fish_voice_id, e)


