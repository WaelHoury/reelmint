"""RQ task for executing publish jobs in the dedicated video_publish queue."""

import logging
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from redis import Redis

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import PublishJob, SocialAccount, Video
from app.api.services.token_encryption import encrypt_token, decrypt_token
from app.social import youtube
from app.social.youtube import AuthError, QuotaExceededError, RetryableYouTubeError, YouTubeError

logger = logging.getLogger(__name__)

PUBLISH_QUEUE = "video_publish"
PUBLISH_TASK = "app.api.services.publish_worker_task.execute_publish_job"
QUOTA_ERROR_CODE = "quota_exceeded"
# YouTube Data API quotas reset at midnight Pacific time; retry a few minutes after.
QUOTA_RESET_TZ = "America/Los_Angeles"
QUOTA_RESET_AT = time(0, 5)  # local Pacific wall-clock time
# A scheduled publishAt must be in the future when the upload finally happens.
PUBLISH_AT_MIN_LEAD = timedelta(minutes=2)
PUBLISH_AT_FALLBACK = timedelta(minutes=15)


def next_quota_reset(now: Optional[datetime] = None) -> datetime:
    """Aware UTC time of the next YouTube quota reset (00:00 Pacific) plus 5 minutes.

    DST-safe: the wall-clock time 00:05 is resolved in America/Los_Angeles for
    the right date, so it is 07:05 or 08:05 UTC depending on the season.
    ``now`` may be naive (treated as UTC) or aware.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(QUOTA_RESET_TZ)
    local_now = now.astimezone(tz)
    reset_wall = QUOTA_RESET_AT
    candidate = datetime.combine(local_now.date(), reset_wall, tzinfo=tz)
    if candidate <= local_now:
        candidate = datetime.combine(local_now.date() + timedelta(days=1), reset_wall, tzinfo=tz)
    return candidate.astimezone(timezone.utc)


def is_quota_error(exc: Exception) -> bool:
    """YouTube quota/upload-limit error (by type, or our error text mentions quota)."""
    if isinstance(exc, QuotaExceededError):
        return True
    return isinstance(exc, YouTubeError) and "quota" in str(exc).lower()


def _batch_item_for_job(db, job: PublishJob):
    """(BatchItem, Batch) that started this publish job, or (None, None)."""
    from app.db.models import Batch, BatchItem
    item = db.query(BatchItem).filter(BatchItem.publish_job_id == job.id).first()
    if item is None:
        return None, None
    return item, db.get(Batch, item.batch_id)


def _is_hands_off(db, job: PublishJob) -> bool:
    try:
        _item, batch = _batch_item_for_job(db, job)
        return bool(batch is not None and batch.hands_off)
    except Exception:
        logger.exception("Could not look up the batch for publish job %s", job.id)
        return False


def _ensure_future_publish_at(db, job: PublishJob) -> None:
    """Move a scheduled publishAt that has passed (e.g. after a quota wait) into the future."""
    payload = dict(job.request_payload or {})
    raw = payload.get("publish_at")
    if not raw:
        return
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if when > now + PUBLISH_AT_MIN_LEAD:
        return
    new_when = now + PUBLISH_AT_FALLBACK
    payload["publish_at"] = new_when.strftime("%Y-%m-%dT%H:%M:%SZ")
    job.request_payload = payload
    item, _batch = _batch_item_for_job(db, job)
    if item is not None:
        item.scheduled_publish_at = new_when.replace(tzinfo=None)
    db.commit()
    logger.info("Publish job %s: scheduled time had passed; publishing at %s instead",
                job.id, payload["publish_at"])


def _handle_quota_error(db, job: PublishJob, exc: Exception, redis_conn: Redis,
                        now: Optional[datetime] = None) -> None:
    """Hands-off batches wait for the quota reset and retry; everything else fails clearly."""
    if _is_hands_off(db, job):
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        reset = next_quota_reset(now)
        delay = reset - now
        _update_job(
            db, job,
            status="queued",
            error_code=QUOTA_ERROR_CODE,
            error_message=str(exc),
            provider_status="Waiting for YouTube quota reset",
            next_retry_at=reset.replace(tzinfo=None),
        )
        from rq import Queue
        Queue(PUBLISH_QUEUE, connection=redis_conn).enqueue_in(
            max(delay, timedelta(seconds=1)), PUBLISH_TASK, job.id, job_timeout="30m",
        )
        logger.warning("Publish job %s: YouTube quota exceeded; retrying at %s UTC",
                       job.id, reset.strftime("%Y-%m-%d %H:%M"))
        return
    _update_job(
        db, job,
        status="failed",
        error_code=QUOTA_ERROR_CODE,
        error_message=(
            "YouTube's daily upload quota is used up. It resets at midnight Pacific time; "
            f"retry after that. ({exc})"
        )[:1000],
    )


def _resolve_video_path(video: Video) -> tuple[Path, bool]:
    """Resolve the local path to a video file.

    Returns (local_path, is_temp). If is_temp=True, caller must clean up.
    """
    if settings.use_r2_storage and video.r2_video_key:
        from app.api.services.r2_storage import r2_storage

        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False, dir=settings.temp_dir)
        tmp.close()
        tmp_path = Path(tmp.name)
        logger.info("Downloading video from R2: %s → %s", video.r2_video_key, tmp_path)
        r2_storage.download_file(video.r2_video_key, tmp_path)
        return tmp_path, True

    local_path = Path(settings.output_dir) / video.output_filename
    if not local_path.exists():
        raise FileNotFoundError(f"Video file not found: {local_path}")
    return local_path, False


def _refresh_if_needed(account: SocialAccount, db, redis_conn: Redis) -> str:
    """Check token expiry and refresh if within 5-min window.

    Uses a Redis lock to prevent concurrent refresh races.
    Returns the current (possibly refreshed) plaintext access token.
    """
    access_token = decrypt_token(account.access_token_enc)

    # If no expiry set or still valid beyond 5-min buffer, return as-is
    if account.token_expires_at and account.token_expires_at > datetime.utcnow() + timedelta(minutes=5):
        return access_token

    if not account.refresh_token_enc:
        raise AuthError("Token expired and no refresh token available")

    # Acquire lock to prevent concurrent refreshes on the same account
    lock_key = f"social_refresh:{account.id}"
    lock = redis_conn.lock(lock_key, timeout=30)

    if lock.acquire(blocking=True, blocking_timeout=10):
        try:
            # Re-read account from DB in case another worker just refreshed
            db.refresh(account)
            if account.token_expires_at and account.token_expires_at > datetime.utcnow() + timedelta(minutes=5):
                return decrypt_token(account.access_token_enc)

            # Do the refresh
            refresh_tok = decrypt_token(account.refresh_token_enc)
            result = youtube.refresh_token(refresh_tok)

            new_access = result["access_token"]
            expires_in = result.get("expires_in", 3600)

            account.access_token_enc = encrypt_token(new_access)
            account.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            account.updated_at = datetime.utcnow()
            db.commit()

            logger.info("Refreshed token for social account %s", account.id)
            return new_access
        finally:
            lock.release()
    else:
        # Lock acquisition timed out — re-read and try the token
        db.refresh(account)
        return decrypt_token(account.access_token_enc)


def _update_job(db, job: PublishJob, **kwargs):
    """Update publish job fields and commit."""
    for key, val in kwargs.items():
        setattr(job, key, val)
    job.updated_at = datetime.utcnow()
    db.commit()


def execute_publish_job(publish_job_id: int):
    """Execute a publish job — main RQ task entry point."""
    db = SessionLocal()
    redis_conn = Redis.from_url(settings.redis_url)
    tmp_path = None

    try:
        job = db.query(PublishJob).filter_by(id=publish_job_id).first()
        if not job:
            logger.error("Publish job %s not found", publish_job_id)
            return

        account = db.query(SocialAccount).filter_by(id=job.social_account_id).first()
        if not account:
            _update_job(db, job, status="failed", error_message="Social account not found")
            return

        video = db.query(Video).filter_by(id=job.video_id).first()
        if not video:
            _update_job(db, job, status="failed", error_message="Video not found")
            return

        _update_job(db, job, status="uploading")
        _ensure_future_publish_at(db, job)

        # Resolve video file
        video_path, is_temp = _resolve_video_path(video)
        if is_temp:
            tmp_path = video_path

        # Refresh token if needed
        access_token = _refresh_if_needed(account, db, redis_conn)

        # Progress callback
        def on_progress(pct, msg):
            _update_job(db, job, provider_status=msg)

        # Upload based on provider
        if job.provider == "youtube":
            result = youtube.upload_video(access_token, video_path, job.request_payload or {}, on_progress)
        else:
            _update_job(db, job, status="failed", error_message=f"Unsupported provider: {job.provider}")
            return

        # Success
        _update_job(
            db, job,
            status="published",
            platform_media_id=result.get("platform_media_id"),
            platform_post_url=result.get("platform_post_url"),
            provider_status=result.get("provider_status", "uploaded"),
            published_at=datetime.utcnow(),
            error_code=None,
            error_message=None,
            next_retry_at=None,
        )
        logger.info("Publish job %s completed: %s", job.id, result.get("platform_post_url"))

    except QuotaExceededError as e:
        logger.warning("Publish job %s quota error: %s", publish_job_id, e)
        _handle_quota_error(db, job, e, redis_conn)

    except AuthError as e:
        logger.warning("Publish job %s auth error: %s", publish_job_id, e)
        _update_job(db, job, status="reconnect_required", error_code="auth_error", error_message=str(e))
        # Mark account as expired
        if account:
            account.status = "expired"
            account.updated_at = datetime.utcnow()
            db.commit()

    except RetryableYouTubeError as e:
        logger.warning("Publish job %s retryable error: %s", publish_job_id, e)
        if is_quota_error(e):
            _handle_quota_error(db, job, e, redis_conn)
        elif job.retry_count < job.max_retries:
            delay = min(2 ** job.retry_count * 30, 3600)
            _update_job(
                db, job,
                status="queued",
                error_message=str(e),
                retry_count=job.retry_count + 1,
                next_retry_at=datetime.utcnow() + timedelta(seconds=delay),
            )
            # Re-enqueue with delay
            from rq import Queue
            queue = Queue(PUBLISH_QUEUE, connection=redis_conn)
            queue.enqueue_in(
                timedelta(seconds=delay),
                PUBLISH_TASK,
                publish_job_id,
                job_timeout="30m",
            )
            logger.info("Publish job %s re-enqueued with %ss delay (retry #%s)", job.id, delay, job.retry_count)
        else:
            _update_job(db, job, status="failed", error_code="max_retries", error_message=str(e))

    except YouTubeError as e:
        if is_quota_error(e):
            logger.warning("Publish job %s quota error: %s", publish_job_id, e)
            _handle_quota_error(db, job, e, redis_conn)
        else:
            logger.exception("Publish job %s failed: %s", publish_job_id, e)
            _update_job(db, job, status="failed", error_code="youtube_error", error_message=str(e))

    except Exception as e:
        logger.exception("Publish job %s failed: %s", publish_job_id, e)
        try:
            _update_job(db, job, status="failed", error_code="unexpected", error_message=str(e))
        except Exception:
            logger.exception("Failed to update job status")

    finally:
        # Clean up temp file if downloaded from R2
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        db.close()
