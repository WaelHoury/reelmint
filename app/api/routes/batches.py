"""Batch queue / autopilot routes: queue many videos, produce them one at a time."""

import logging
import os
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.schemas.batch import (
    AddBatchItemsRequest,
    BatchCounts,
    BatchItemResponse,
    BatchListResponse,
    BatchResponse,
    BatchSummary,
    CreateBatchRequest,
    SuggestTopicsRequest,
    SuggestTopicsResponse,
    YouTubeQuotaResponse,
)
from app.api.services import batch_service
from app.api.services.batch_service import BatchError, iso_utc
from app.api.services.session_store import session_store
from app.db.base import get_db
from app.db.models import Batch, BatchItem, PublishJob, SocialAccount, User

logger = logging.getLogger(__name__)


def format_validation_error(exc: RequestValidationError) -> str:
    """Turn the first pydantic error into one sentence a user can act on."""
    errors = exc.errors()
    if not errors:
        return "Invalid request."
    err = errors[0]
    msg = str(err.get("msg") or "Invalid value")
    for prefix in ("Value error, ", "Assertion failed, "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    loc = [p for p in err.get("loc", ()) if p != "body"]
    if len(loc) >= 2 and loc[0] == "items" and isinstance(loc[1], int):
        return f"Video {loc[1] + 1}: {msg}"
    if err.get("type", "").startswith(("value_error", "assertion_error")) or not loc:
        return msg
    field = ".".join(str(p) for p in loc)
    return f"{field}: {msg}"


class FriendlyValidationRoute(APIRoute):
    """Return 400 {"detail": "<sentence>"} for invalid bodies instead of FastAPI's 422 list."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                return JSONResponse(status_code=400, content={"detail": format_validation_error(exc)})

        return handler


router = APIRouter(prefix="/batches", tags=["batches"], route_class=FriendlyValidationRoute)

def _daily_upload_limit():
    """Optional YOUTUBE_DAILY_UPLOAD_LIMIT; the UI warns when a schedule exceeds it."""
    raw = os.getenv("YOUTUBE_DAILY_UPLOAD_LIMIT", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


YOUTUBE_QUOTA_NOTE = (
    "Uploads stay private until your Google Cloud project passes YouTube's API compliance "
    "audit, and your project's YouTube Data API quota limits how many videos you can upload "
    "per day. Check both in the Google Cloud Console before automating a channel."
)


# ── Helpers ───────────────────────────────────────────────────


def _raise(e: BatchError):
    raise HTTPException(status_code=e.status_code, detail=e.message)


def _get_batch(db: Session, batch_id: int, user: User) -> Batch:
    batch = db.query(Batch).filter(Batch.id == batch_id, Batch.user_id == user.id).first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


def _get_item(batch: Batch, item_id: int) -> BatchItem:
    for item in batch.items:
        if item.id == item_id:
            return item
    raise HTTPException(status_code=404, detail="Video not found in this batch")


def _live_progress(db: Session, item: BatchItem):
    """(progress_percent, current_step) for an item that is being worked on."""
    if item.status == "publishing":
        if item.publish_job_id:
            job = db.get(PublishJob, item.publish_job_id)
            step = (
                batch_service.quota_wait_step(job)
                or (job.provider_status if job else None)
                or "Waiting for the YouTube upload..."
            )
            return None, step
        return None, "Preparing YouTube title and description..."
    if item.status not in batch_service.RUNNER_STAGES or not item.session_id:
        return None, None
    try:
        session = session_store.get(item.session_id)
    except Exception:
        return None, None
    if session is None:
        return None, None
    if item.status == "scripting":
        return session.progress_percent, session.current_step or "Writing the script..."
    if item.status == "finding_clips":
        clips = session.clips or []
        done = sum(1 for c in clips if (c or {}).get("search_status") in ("ready", "failed"))
        step = f"Finding footage ({done}/{len(clips)} clips)" if clips else "Planning shots..."
        return None, step
    return session.progress_percent, session.current_step


def _summary_fields(batch: Batch) -> dict:
    items = list(batch.items)
    return dict(
        id=batch.id,
        name=batch.name,
        status=batch.status,
        created_at=iso_utc(batch.created_at),
        total=len(items),
        counts=BatchCounts(**batch_service.counts_for(items)),
        publish_enabled=bool((batch.publish or {}).get("enabled")),
        hands_off=bool(batch.hands_off),
    )


def _item_response(db: Session, item: BatchItem) -> BatchItemResponse:
    in_progress = item.status in batch_service.IN_PROGRESS_STATUSES
    progress, step = _live_progress(db, item) if in_progress else (None, None)
    next_attempt_at = item.next_attempt_at if item.status == "pending" else None
    if next_attempt_at is not None:
        step = (f"Retrying automatically (attempt {(item.attempts or 0) + 1} of "
                f"{batch_service.HANDS_OFF_MAX_ATTEMPTS})")
    elif item.status == "publishing" and item.publish_job_id:
        next_attempt_at = batch_service.publish_wait_until(db.get(PublishJob, item.publish_job_id))
    return BatchItemResponse(
        id=item.id,
        position=item.position,
        input_type=item.input_type,
        title=item.title,
        text_preview=(item.input_text or "")[:160],
        status=item.status,
        progress_percent=progress,
        current_step=step,
        video_id=item.video_id,
        youtube_url=item.youtube_url,
        scheduled_publish_at=iso_utc(item.scheduled_publish_at),
        error_message=item.error_message,
        attempts=item.attempts or 0,
        next_attempt_at=iso_utc(next_attempt_at),
        started_at=iso_utc(item.started_at),
        finished_at=iso_utc(item.finished_at),
    )


def _batch_response(db: Session, batch: Batch) -> BatchResponse:
    batch_service.reconcile_publish_status(db, [batch])
    db.refresh(batch)
    return BatchResponse(
        **_summary_fields(batch),
        settings=batch.settings or {},
        publish=batch.publish or {},
        items=[_item_response(db, i) for i in batch.items],
    )


def _validate_publish_account(db: Session, user: User, account_id: int) -> None:
    account = db.query(SocialAccount).filter_by(id=account_id, user_id=user.id).first()
    if account is None:
        raise HTTPException(
            status_code=400,
            detail="The selected YouTube account isn't connected. Connect it on the Account page.",
        )
    if account.provider != "youtube":
        raise HTTPException(status_code=400, detail="Batch publishing supports YouTube accounts only.")
    if account.status != "active":
        name = account.display_name or account.username or "YouTube account"
        raise HTTPException(
            status_code=400,
            detail=f"'{name}' needs to be reconnected on the Account page (status: {account.status}).",
        )


# ── Routes ────────────────────────────────────────────────────


@router.post("", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
def create_batch(
    request: CreateBatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a batch of queued videos (optionally starting it right away)."""
    settings_data = request.settings.model_dump()
    if settings_data.get("tts_voice_key"):
        from app.api.services.tts_service import resolve_tts_selection_for_user
        voice = resolve_tts_selection_for_user(
            provider=settings_data.get("tts_provider"),
            voice_key=settings_data["tts_voice_key"],
            user_id=user.id,
        )
        settings_data["tts_provider"] = voice.provider
        settings_data["tts_voice_key"] = voice.voice_key

    publish_data = request.publish.model_dump()
    if request.publish.enabled:
        _validate_publish_account(db, user, request.publish.account_id)
    if not publish_data.get("schedule"):
        publish_data["schedule"] = {"mode": "immediate", "start_at": None, "interval_minutes": None}

    items = [i.model_dump() for i in request.items]
    if not items and request.theme is not None:
        try:
            topics = batch_service.suggest_topics(
                db, user.id, request.theme.niche, request.theme.count,
                style=settings_data.get("narration_style"),
            )
        except BatchError as e:
            _raise(e)
        items = [{"input_type": "prompt", "text": t, "title": None} for t in topics]

    batch = Batch(
        user_id=user.id,
        name=request.name,
        status="draft",
        settings=settings_data,
        publish=publish_data,
        hands_off=bool(request.hands_off),
    )
    db.add(batch)
    try:
        batch_service.add_items(batch, items)
    except BatchError as e:
        _raise(e)
    db.commit()
    db.refresh(batch)

    if request.start:
        try:
            batch_service.start_batch(db, batch)
        except BatchError as e:
            _raise(e)
    return _batch_response(db, batch)


@router.get("", response_model=BatchListResponse)
def list_batches(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List the user's batches, newest first."""
    batches = (
        db.query(Batch)
        .filter(Batch.user_id == user.id)
        .order_by(Batch.created_at.desc(), Batch.id.desc())
        .all()
    )
    batch_service.reconcile_publish_status(db, batches)
    return BatchListResponse(batches=[BatchSummary(**_summary_fields(b)) for b in batches])


@router.post("/suggest-topics", response_model=SuggestTopicsResponse)
def suggest_topics(
    request: SuggestTopicsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Brainstorm fresh video topics for a niche (skips ones the user already has)."""
    try:
        topics = batch_service.suggest_topics(db, user.id, request.niche, request.count, request.style)
    except BatchError as e:
        _raise(e)
    return SuggestTopicsResponse(topics=topics)


@router.get("/youtube-quota", response_model=YouTubeQuotaResponse)
def youtube_quota():
    """Rough YouTube upload capacity with the default API quota."""
    return YouTubeQuotaResponse(uploads_per_day_estimate=_daily_upload_limit(), note=YOUTUBE_QUOTA_NOTE)


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _batch_response(db, _get_batch(db, batch_id, user))


@router.post("/{batch_id}/start", response_model=BatchResponse)
def start_batch(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = _get_batch(db, batch_id, user)
    publish = batch.publish or {}
    if publish.get("enabled") and publish.get("account_id") is not None:
        _validate_publish_account(db, user, publish["account_id"])
    try:
        batch_service.start_batch(db, batch)
    except BatchError as e:
        _raise(e)
    return _batch_response(db, batch)


@router.post("/{batch_id}/pause", response_model=BatchResponse)
def pause_batch(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = _get_batch(db, batch_id, user)
    try:
        batch_service.pause_batch(db, batch)
    except BatchError as e:
        _raise(e)
    return _batch_response(db, batch)


@router.post("/{batch_id}/cancel", response_model=BatchResponse)
def cancel_batch(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = _get_batch(db, batch_id, user)
    try:
        batch_service.cancel_batch(db, batch)
    except BatchError as e:
        _raise(e)
    return _batch_response(db, batch)


@router.post("/{batch_id}/items", response_model=BatchResponse)
def add_batch_items(
    batch_id: int,
    request: AddBatchItemsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Append videos to the end of a batch (a completed batch reopens as paused)."""
    batch = _get_batch(db, batch_id, user)
    if batch.status == "cancelled":
        raise HTTPException(status_code=400, detail="This batch was cancelled. Create a new batch instead.")
    try:
        batch_service.add_items(batch, [i.model_dump() for i in request.items])
    except BatchError as e:
        _raise(e)
    if batch.status == "completed":
        batch.status = "paused"
    batch.updated_at = batch_service.utcnow()
    db.commit()
    return _batch_response(db, batch)


@router.post("/{batch_id}/items/{item_id}/retry", response_model=BatchResponse)
def retry_batch_item(
    batch_id: int, item_id: int,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    batch = _get_batch(db, batch_id, user)
    batch_service.reconcile_publish_status(db, [batch])
    item = _get_item(batch, item_id)
    try:
        batch_service.retry_item(db, batch, item)
    except BatchError as e:
        _raise(e)
    return _batch_response(db, batch)


@router.delete("/{batch_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch_item(
    batch_id: int, item_id: int,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    batch = _get_batch(db, batch_id, user)
    item = _get_item(batch, item_id)
    try:
        batch_service.delete_item(db, batch, item)
    except BatchError as e:
        _raise(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = _get_batch(db, batch_id, user)
    try:
        batch_service.delete_batch(db, batch)
    except BatchError as e:
        _raise(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
