"""Batch queue / autopilot.

A batch is an ordered list of videos (topics or full scripts). Items are
produced strictly one at a time by ``run_next_item``, an RQ job on the
``video_generation`` queue that runs the normal wizard pipeline for one item
(script -> clips -> render) and then chains itself for the next item.

After an item renders it is optionally handed to the existing publish worker
(YouTube upload, asynchronous). ``reconcile_publish_status`` maps the publish
job's outcome back onto the item when the batch is read.

Only one runner per batch can be active at a time: it holds the Redis lock
``batch_runner:{batch_id}`` while it works.
"""

import importlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from redis import Redis
from rq import Queue

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import Batch, BatchItem, PublishJob, Video
from app.api.services.session_store import VideoSession, session_store
from app.utils.helpers import derive_video_title

# The module, not the ``video_service`` instance the package re-exports under the same name.
vs = importlib.import_module("app.api.services.video_service")

logger = logging.getLogger(__name__)

QUEUE_NAME = "video_generation"
RUNNER_JOB = "app.api.services.batch_service.run_next_item"
RUNNER_JOB_TIMEOUT = "60m"
RUNNER_LOCK_PREFIX = "batch_runner:"
RUNNER_QUEUED_PREFIX = "batch_runner_queued:"
RUNNER_LOCK_TTL = 70 * 60  # seconds; refreshed while the runner is alive
LOCK_REFRESH_SECONDS = 60
STALE_SECONDS = 120  # recovery threshold for a dead runner

MAX_ITEMS = 100
YOUTUBE_DESCRIPTION_MAX = 5000
SCHEDULE_PAST_FALLBACK = timedelta(minutes=15)

# Item statuses
PENDING = "pending"
RUNNER_STAGES = ("scripting", "finding_clips", "rendering")
IN_PROGRESS_STATUSES = RUNNER_STAGES + ("publishing",)
FINISHED_OK = ("done", "published")

# Hands-off autopilot
HANDS_OFF_MAX_ATTEMPTS = 3  # attempts in total, incl. the first
HANDS_OFF_BACKOFF = (timedelta(minutes=2), timedelta(minutes=10))
HANDS_OFF_POLL = timedelta(minutes=5)  # how often to check on uploads in flight

STAGE_LABELS = {
    "scripting": "Writing the script",
    "finding_clips": "Finding footage",
    "rendering": "Rendering",
    "publishing": "Preparing the YouTube upload",
}


class BatchError(Exception):
    """A user-facing error raised by batch operations (mapped to an HTTP error)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BatchItemError(Exception):
    """A pipeline failure whose message is already fit to show to the user."""

    retryable = True


class PermanentItemError(BatchItemError):
    """A failure only the user can fix (voice gone, account disconnected): never auto-retried."""

    retryable = False


# ── Time helpers ──────────────────────────────────────────────


def utcnow() -> datetime:
    """Naive UTC 'now' (the DB stores naive UTC datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_utc(value) -> Optional[datetime]:
    """Parse an ISO 8601 string/datetime into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a (naive UTC or aware) datetime as ISO 8601 with a Z suffix."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


def rfc3339(dt: datetime) -> str:
    """Format for YouTube's status.publishAt."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_publish_at(schedule: Optional[dict], k: int, now: Optional[datetime] = None) -> Optional[datetime]:
    """Scheduled publish time for the k-th published item (0-based), or None.

    Interval mode: ``start_at + k * interval_minutes``. If that moment has
    already passed, the video is scheduled 15 minutes from now instead
    (YouTube needs a future publishAt). Immediate mode returns None.
    """
    if not schedule or schedule.get("mode") != "interval":
        return None
    start = parse_utc(schedule.get("start_at"))
    interval = int(schedule.get("interval_minutes") or 0)
    if start is None or interval <= 0:
        return None
    now = parse_utc(now) if now is not None else datetime.now(timezone.utc)
    slot = start + timedelta(minutes=interval * max(0, int(k)))
    if slot <= now:
        slot = now + SCHEDULE_PAST_FALLBACK
    return slot


# ── Redis: runner lock and queue ──────────────────────────────

_redis_conn: Optional[Redis] = None


def get_redis() -> Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = Redis.from_url(settings.redis_url)
    return _redis_conn


def _decode(value) -> Optional[str]:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def _lock_key(batch_id: int) -> str:
    return f"{RUNNER_LOCK_PREFIX}{batch_id}"


def _queued_key(batch_id: int) -> str:
    return f"{RUNNER_QUEUED_PREFIX}{batch_id}"


def _runner_token() -> str:
    """Lock value: the RQ job id when running in a worker (lets recovery detect dead jobs)."""
    try:
        from rq import get_current_job
        job = get_current_job()
    except Exception:
        job = None
    if job is not None:
        return f"rq:{job.id}"
    return f"local:{uuid.uuid4()}"


def acquire_runner_lock(r: Redis, batch_id: int, token: str) -> bool:
    return bool(r.set(_lock_key(batch_id), token, nx=True, ex=RUNNER_LOCK_TTL))


def release_runner_lock(r: Redis, batch_id: int, token: str) -> None:
    try:
        if _decode(r.get(_lock_key(batch_id))) == token:
            r.delete(_lock_key(batch_id))
    except Exception:
        logger.warning("Could not release runner lock for batch %s", batch_id)


def _job_status(r: Redis, job_id: str) -> Optional[str]:
    """RQ status string for a job id, or None if the job no longer exists."""
    from rq.job import Job
    try:
        status = Job.fetch(job_id, connection=r).get_status()
    except Exception:
        return None
    if status is None:
        return None
    return getattr(status, "value", str(status))


def runner_lock_held(r: Redis, batch_id: int) -> bool:
    """True if a live runner holds the batch lock.

    A lock whose RQ job has died (failed/stopped/missing) is cleared.
    """
    token = _decode(r.get(_lock_key(batch_id)))
    if not token:
        return False
    if token.startswith("rq:"):
        status = _job_status(r, token[3:])
        if status not in ("queued", "started", "deferred", "scheduled"):
            if _decode(r.get(_lock_key(batch_id))) == token:
                r.delete(_lock_key(batch_id))
            logger.warning("Cleared stale runner lock for batch %s (job status: %s)", batch_id, status)
            return False
    return True


class _LockKeeper:
    """Refreshes the runner lock TTL while an item is processed."""

    def __init__(self, r: Redis, batch_id: int, token: str):
        self.r, self.batch_id, self.token = r, batch_id, token
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(LOCK_REFRESH_SECONDS):
            try:
                if _decode(self.r.get(_lock_key(self.batch_id))) == self.token:
                    self.r.expire(_lock_key(self.batch_id), RUNNER_LOCK_TTL)
            except Exception:
                logger.warning("Could not refresh runner lock for batch %s", self.batch_id)

    def start(self) -> "_LockKeeper":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()


def queued_runner_job(r: Redis, batch_id: int) -> Optional[str]:
    """Id of a runner job for this batch that is still waiting in the queue, if any."""
    existing = _decode(r.get(_queued_key(batch_id)))
    if existing and _job_status(r, existing) in ("queued", "deferred", "scheduled"):
        return existing
    return None


def _scheduled_time(r: Redis, job_id: str) -> Optional[datetime]:
    """Aware UTC time a scheduled (``enqueue_in``) job will run, or None."""
    from rq.job import Job
    from rq.registry import ScheduledJobRegistry
    try:
        job = Job.fetch(job_id, connection=r)
        when = ScheduledJobRegistry(queue=Queue(QUEUE_NAME, connection=r)).get_scheduled_time(job)
    except Exception:
        return None
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _drop_scheduled_job(r: Redis, job_id: str) -> None:
    from rq.job import Job
    from rq.registry import ScheduledJobRegistry
    try:
        job = Job.fetch(job_id, connection=r)
        ScheduledJobRegistry(queue=Queue(QUEUE_NAME, connection=r)).remove(job)
        job.delete()
    except Exception:
        logger.warning("Could not drop scheduled runner job %s", job_id)


def enqueue_runner(batch_id: int, r: Optional[Redis] = None, at: Optional[datetime] = None) -> str:
    """Queue ``run_next_item(batch_id)`` unless a runner job for it is already waiting.

    ``at`` (aware or naive UTC) schedules a wake-up with ``enqueue_in`` instead,
    used by hands-off batches waiting for a retry. A pending wake-up is
    replaced when an earlier run is wanted, so at most one runner job waits.
    """
    r = r or get_redis()
    existing = queued_runner_job(r, batch_id)
    if existing:
        if _job_status(r, existing) != "scheduled":
            return existing  # runs right away; it re-plans any wake-up itself
        if at is not None:
            when = _scheduled_time(r, existing)
            if when is not None and when <= parse_utc(at) + timedelta(seconds=5):
                return existing
        _drop_scheduled_job(r, existing)
    queue = Queue(QUEUE_NAME, connection=r)
    if at is None:
        job = queue.enqueue(RUNNER_JOB, batch_id, job_timeout=RUNNER_JOB_TIMEOUT)
        ttl = 24 * 3600
    else:
        delay = max(parse_utc(at) - datetime.now(timezone.utc), timedelta(seconds=1))
        job = queue.enqueue_in(delay, RUNNER_JOB, batch_id, job_timeout=RUNNER_JOB_TIMEOUT)
        ttl = int(delay.total_seconds()) + 24 * 3600
    r.set(_queued_key(batch_id), job.id, ex=ttl)
    return job.id


# ── Runner ────────────────────────────────────────────────────


def _is_runner_active(item: BatchItem) -> bool:
    """Item is being worked on by the runner (not just waiting on the publish worker)."""
    if item.status in RUNNER_STAGES:
        return True
    return item.status == "publishing" and not item.publish_job_id


def _short_error(exc: Exception, stage: Optional[str]) -> str:
    if isinstance(exc, BatchItemError):
        message = str(exc)
    else:
        detail = (str(exc) or exc.__class__.__name__).strip().splitlines()[0]
        label = STAGE_LABELS.get(stage or "", "Processing")
        message = f"{label} failed: {detail}"
    return message[:500]


def _current_job_id() -> Optional[str]:
    try:
        from rq import get_current_job
        job = get_current_job()
    except Exception:
        job = None
    return job.id if job is not None else None


def _ready_filter(query, now: datetime):
    """Pending items whose retry time (if any) has come."""
    return query.filter(
        BatchItem.status == PENDING,
        (BatchItem.next_attempt_at.is_(None)) | (BatchItem.next_attempt_at <= now),
    )


def _next_wake(db, batch: Batch, now: datetime) -> Optional[datetime]:
    """When the runner should look again at a batch with nothing ready, or None if it is finished.

    Pending items waiting for a retry wake it at the earliest retry time. A
    hands-off batch also waits for uploads in flight (checking every few
    minutes), so a failed upload can still be retried before it completes.
    """
    waits = [i.next_attempt_at for i in batch.items if i.status == PENDING and i.next_attempt_at]
    wake = min(waits) if waits else None
    if batch.hands_off and any(i.status in IN_PROGRESS_STATUSES for i in batch.items):
        poll = now + HANDS_OFF_POLL
        wake = min(wake, poll) if wake else poll
    if wake is None and any(i.status == PENDING for i in batch.items):
        wake = now  # a pending item without a retry time: run now
    return wake


def run_next_item(batch_id: int) -> None:
    """RQ job: produce the next pending item of a batch, then chain to the next one."""
    r = get_redis()
    try:
        # This queued runner is now running (leave a different, scheduled wake-up alone).
        own = _current_job_id()
        queued = _decode(r.get(_queued_key(batch_id)))
        if queued is not None and (queued == own or _job_status(r, queued) != "scheduled"):
            r.delete(_queued_key(batch_id))
    except Exception:
        pass

    token = _runner_token()
    if not acquire_runner_lock(r, batch_id, token):
        logger.info("Batch %s: another runner is active; exiting", batch_id)
        return

    keeper = _LockKeeper(r, batch_id, token).start()
    chain = False
    db = SessionLocal()
    try:
        batch = db.get(Batch, batch_id)
        if batch is None or batch.status != "running":
            return
        if batch.hands_off:
            # Failed uploads become automatic retries before we look for work.
            reconcile_publish_status(db, [batch])
        now = utcnow()
        item = (
            _ready_filter(db.query(BatchItem).filter(BatchItem.batch_id == batch_id), now)
            .order_by(BatchItem.position, BatchItem.id)
            .first()
        )
        if item is None:
            wake = _next_wake(db, batch, now)
            if wake is not None:
                enqueue_runner(batch_id, r, at=wake)
                logger.info("Batch %s: nothing ready; checking again at %s UTC", batch_id, iso_utc(wake))
                return
            batch.status = "completed"
            batch.updated_at = utcnow()
            db.commit()
            logger.info("Batch %s completed", batch_id)
            return

        _run_item(db, batch, item)
        chain = True
    except Exception:
        logger.exception("Batch %s runner crashed", batch_id)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        keeper.stop()
        release_runner_lock(r, batch_id, token)
        if chain:
            try:
                db.expire_all()
                batch = db.get(Batch, batch_id)
                if batch is not None and batch.status == "running":
                    enqueue_runner(batch_id, r)
            except Exception:
                logger.exception("Batch %s: could not queue the next item", batch_id)
        db.close()


def _run_item(db, batch: Batch, item: BatchItem) -> None:
    """Run one item end to end. Never raises: failures mark the item failed."""
    now = utcnow()
    item.status = "scripting"
    item.started_at = now
    item.finished_at = None
    item.error_message = None
    item.next_attempt_at = None
    item.attempts = (item.attempts or 0) + 1
    batch.updated_at = now
    db.commit()
    item_id, batch_id = item.id, batch.id
    logger.info("Batch %s: starting item %s (position %s)", batch.id, item_id, item.position)

    try:
        _process_item(db, batch, item)
    except Exception as exc:
        stage = None
        try:
            stage = item.status
        except Exception:
            pass
        logger.exception("Batch %s: item %s failed during %s", batch_id, item_id, stage)
        db.rollback()
        item = db.get(BatchItem, item_id)
        batch = db.get(Batch, batch_id)
        if item is not None:
            fail_or_retry(batch, item, _short_error(exc, stage),
                          retryable=getattr(exc, "retryable", True))
        if batch is not None:
            batch.updated_at = utcnow()
        db.commit()
        return

    batch.updated_at = utcnow()
    db.commit()


def fail_or_retry(batch: Optional[Batch], item: BatchItem, message: str,
                  retryable: bool = True, now: Optional[datetime] = None) -> bool:
    """Mark an item failed, or (hands-off batches) schedule an automatic retry.

    Hands-off items get up to ``HANDS_OFF_MAX_ATTEMPTS`` attempts in total,
    waiting 2 minutes, then 10 minutes between them. Returns True if a retry
    was scheduled. The caller commits.
    """
    now = now or utcnow()
    attempts = item.attempts or 0
    if (batch is not None and batch.hands_off and retryable
            and attempts < HANDS_OFF_MAX_ATTEMPTS):
        delay = HANDS_OFF_BACKOFF[min(max(attempts, 1), len(HANDS_OFF_BACKOFF)) - 1]
        item.status = PENDING
        item.next_attempt_at = now + delay
        item.finished_at = None
        item.error_message = (
            f"Attempt {max(attempts, 1)} of {HANDS_OFF_MAX_ATTEMPTS} failed: {message} "
            "Retrying automatically."
        )[:500]
        logger.warning("Batch %s: item %s failed (attempt %s); retrying at %s UTC",
                       batch.id, item.id, attempts, iso_utc(item.next_attempt_at))
        return True
    item.status = "failed"
    item.error_message = (message or "Failed")[:500]
    item.finished_at = now
    item.next_attempt_at = None
    return False


def _set_stage(db, item: BatchItem, status: str) -> None:
    item.status = status
    db.commit()


def _process_item(db, batch: Batch, item: BatchItem) -> None:
    pub = batch.publish or {}

    # A retried item whose render already succeeded (publishing failed) reuses the video.
    video = db.get(Video, item.video_id) if item.video_id else None
    session = None
    if video is None:
        session = _create_session(batch, item)
        item.session_id = session.session_id
        db.commit()

        session = _write_script(session)
        _set_stage(db, item, "finding_clips")
        session = _find_clips(session)
        _set_stage(db, item, "rendering")
        session = _render(session)

        video = _find_rendered_video(db, session)
        if video is None:
            raise BatchItemError("The video rendered but could not be saved to the library.")
        item.video_id = video.id
        db.commit()

    if pub.get("enabled"):
        _set_stage(db, item, "publishing")
        _start_publish(db, batch, item, video, session)
    else:
        item.status = "done"
        item.finished_at = utcnow()
        db.commit()


def _resolve_tts(cfg: dict, user_id: int):
    from fastapi import HTTPException
    from app.api.services.tts_service import (
        resolve_default_tts_selection_for_user,
        resolve_tts_selection_for_user,
    )
    try:
        if cfg.get("tts_voice_key"):
            return resolve_tts_selection_for_user(
                provider=cfg.get("tts_provider"),
                voice_key=cfg["tts_voice_key"],
                user_id=user_id,
            )
        return resolve_default_tts_selection_for_user(user_id=user_id)
    except HTTPException as e:
        raise PermanentItemError(f"Voice not available: {e.detail}") from e


def _create_session(batch: Batch, item: BatchItem) -> VideoSession:
    cfg = batch.settings or {}
    is_prompt = item.input_type == "prompt"
    tts = _resolve_tts(cfg, batch.user_id)
    session = session_store.create(
        title=item.title or derive_video_title(item.input_type, item.input_text),
        input_type=item.input_type,
        prompt=item.input_text if is_prompt else None,
        script_text=None if is_prompt else item.input_text,
        duration_seconds=int(cfg.get("duration_seconds") or 60) if is_prompt else None,
        loop_mode=bool(cfg.get("loop_mode", False)),
        subtitles_enabled=bool(cfg.get("subtitles_enabled", True)),
        orientation=cfg.get("orientation") or "landscape",
        narration_style=cfg.get("narration_style"),
        user_id=batch.user_id,
        tts_provider=tts.provider if tts else None,
        tts_voice_key=tts.voice_key if tts else None,
        tts_voice_label=tts.label if tts else None,
    )
    if cfg.get("subtitle_style"):
        session.subtitle_style = cfg["subtitle_style"]
        session_store.update(session)
    return session


def _write_script(session: VideoSession) -> VideoSession:
    session = vs.video_service.generate_script(session)
    if not (session.script_text or "").strip():
        raise BatchItemError("The script came back empty.")
    return vs.video_service.confirm_script(session)


def _find_clips(session: VideoSession) -> VideoSession:
    session.status = "clips_generating"
    session.clips = []
    session.clips_started_at = time.time()
    session.error_message = None
    session_store.update(session)

    vs.generate_clips_task(session.session_id)  # synchronous in this worker

    session = session_store.get(session.session_id)
    if session is None:
        raise BatchItemError("The video session expired while finding footage.")
    if session.status != "clips_ready":
        raise BatchItemError(session.error_message or "Finding footage failed.")
    if not any((c or {}).get("search_status") == "ready" for c in session.clips or []):
        raise BatchItemError("No stock footage matched this video. Try different wording.")
    return session


def _render(session: VideoSession) -> VideoSession:
    try:
        from rq import get_current_job
        job = get_current_job()
    except Exception:
        job = None
    # generate_video_task expects a session that is 'generating'; the job id
    # lets the stale-session sweeper see the render is still alive.
    session.status = "generating"
    session.job_id = job.id if job else None
    session.progress_percent = 0
    session.current_step = "Queued for rendering..."
    session.error_message = None
    session_store.update(session)

    # No per-user generation lock is taken for batch renders, so none can be
    # left held; generate_video_task releases its own on every path.
    vs.generate_video_task(session.session_id)

    session = session_store.get(session.session_id)
    if session is None:
        raise BatchItemError("The video session expired while rendering.")
    if session.status != "completed":
        raise BatchItemError(session.error_message or "Rendering failed.")
    return session


def _find_rendered_video(db, session: VideoSession) -> Optional[Video]:
    """The Video row saved by ``_save_video_to_db`` for this session."""
    if session.project_dir_path:
        try:
            video = db.get(Video, int(Path(session.project_dir_path).name))
            if video is not None and video.user_id == session.user_id:
                return video
        except (TypeError, ValueError):
            pass
    if session.output_path:
        return (
            db.query(Video)
            .filter(
                Video.user_id == session.user_id,
                Video.output_filename == Path(session.output_path).name,
            )
            .order_by(Video.id.desc())
            .first()
        )
    return None


# ── Publishing ────────────────────────────────────────────────


def _get_publish_service():
    from app.api.services.publish_service import publish_service
    return publish_service


def generate_metadata(narration: str, topic: Optional[str], narration_style: Optional[str] = None) -> dict:
    from scene_planner import create_planner, fallback_publish_metadata
    try:
        planner = create_planner(
            narration_style=narration_style or settings.narration_style,
            provider=settings.llm_provider,
        )
        return planner.generate_publish_metadata(narration, topic)
    except Exception:
        logger.exception("Publish metadata generation failed; using fallback")
        return fallback_publish_metadata(narration, topic)


def credits_json_path(output_path) -> Path:
    p = Path(output_path)
    return p.parent / f"{p.stem}_credits.json"


def _parse_credits_txt(text: str) -> List[dict]:
    assets, provider, current = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line in ("LICENSE INFORMATION",) or line.endswith("LICENSE:"):
            break
        if line.isupper() and not line.startswith(("=", "-")) and ":" not in line:
            provider = line.title()
            continue
        if line.startswith("Author:"):
            current = {"author": line[len("Author:"):].strip(), "provider": provider or "Unknown"}
        elif line.startswith("URL:") and current is not None:
            current["video_url"] = line[len("URL:"):].strip()
            assets.append(current)
            current = None
    return assets


def load_credit_assets(paths: Iterable) -> List[dict]:
    """Read render credits (``*_credits.json``, or the ``.txt`` version) into asset dicts."""
    for path in paths:
        if not path:
            continue
        path = Path(path)
        candidates = [path.with_suffix(".json"), path] if path.suffix == ".txt" else [path]
        for candidate in candidates:
            try:
                if not candidate.exists():
                    continue
                if candidate.suffix == ".json":
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    assets = data.get("assets") if isinstance(data, dict) else None
                    if isinstance(assets, list):
                        return [a for a in assets if isinstance(a, dict)]
                else:
                    return _parse_credits_txt(candidate.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Could not read credits file %s", candidate)
    return []


def credit_lines(assets: Iterable[dict]) -> List[str]:
    lines, seen = [], set()
    for asset in assets or []:
        url = (asset.get("video_url") or asset.get("url") or asset.get("author_url") or "").strip()
        author = (asset.get("author") or "Unknown").strip()
        provider = (asset.get("provider") or "").strip()
        key = url or f"{author}|{provider}"
        if key in seen:
            continue
        seen.add(key)
        label = f"Footage: {author}"
        if provider:
            label += f" ({provider})"
        if url:
            label += f" {url}"
        lines.append(re.sub(r"[<>]", "", label))
    return lines


def build_description(base: str, footer: Optional[str], assets: Iterable[dict],
                      limit: int = YOUTUBE_DESCRIPTION_MAX) -> str:
    """Description = LLM text + optional footer + footage credits, capped at ``limit`` chars.

    Credits lines that don't fit are summarized as "...and N more".
    """
    def clean(text):
        return re.sub(r"[<>]", "", (text or "")).strip()

    head = "\n\n".join(p for p in (clean(base), clean(footer)) if p)
    if len(head) > limit:
        return head[:limit - 1].rstrip() + "…"

    lines = credit_lines(assets)
    if not lines:
        return head

    sep = "\n\n" if head else ""
    header = "Credits:"
    suffix_room = len(f"\n…and {len(lines)} more")
    budget = limit - len(head) - len(sep) - len(header) - suffix_room
    included, used = [], 0
    for line in lines:
        if used + 1 + len(line) > budget:
            break
        included.append(line)
        used += 1 + len(line)
    if not included:
        return head
    block = header + "\n" + "\n".join(included)
    omitted = len(lines) - len(included)
    if omitted:
        block += f"\n…and {omitted} more"
    return head + sep + block


def _next_publish_slot(db, batch: Batch, item: BatchItem) -> Optional[datetime]:
    schedule = (batch.publish or {}).get("schedule")
    if not schedule or schedule.get("mode") != "interval":
        return None
    others = (
        db.query(BatchItem)
        .filter(BatchItem.batch_id == batch.id, BatchItem.id != item.id, BatchItem.publish_job_id.isnot(None))
        .all()
    )
    used = {iso_utc(o.scheduled_publish_at) for o in others if o.scheduled_publish_at}
    now = datetime.now(timezone.utc)
    k = len(others)
    slot = compute_publish_at(schedule, k, now)
    while slot is not None and iso_utc(slot) in used and k < len(others) + MAX_ITEMS:
        k += 1
        slot = compute_publish_at(schedule, k, now)
    return slot


def _start_publish(db, batch: Batch, item: BatchItem, video: Video, session: Optional[VideoSession]) -> None:
    from scene_planner import normalize_tags

    pub = batch.publish or {}
    cfg = batch.settings or {}
    narration = video.script_text or (session.script_text if session else None) or item.input_text
    topic = item.title or (item.input_text if item.input_type == "prompt" else None)
    meta = generate_metadata(narration, topic, cfg.get("narration_style"))

    credit_sources = []
    if session is not None and session.credits_path:
        credit_sources.append(session.credits_path)
    if session is not None and session.output_path:
        credit_sources.append(credits_json_path(session.output_path))
    credit_sources.append(credits_json_path(Path(settings.output_dir) / video.output_filename))
    assets = load_credit_assets(credit_sources)

    metadata = {
        "title": meta.get("title") or video.title,
        "description": build_description(meta.get("description", ""), pub.get("description_footer"), assets),
        "tags": normalize_tags(list(pub.get("extra_tags") or []) + list(meta.get("tags") or [])),
        "privacy_status": pub.get("privacy_status") or "private",
        "category_id": str(pub.get("category_id") or "22"),
        "made_for_kids": bool(pub.get("made_for_kids", False)),
    }
    publish_at = _next_publish_slot(db, batch, item)
    if publish_at is not None:
        metadata["publish_at"] = rfc3339(publish_at)

    from app.api.services.publish_service import PublishServiceError
    try:
        job = _get_publish_service().create_publish_job(
            batch.user_id, video.id, pub.get("account_id"), "upload", metadata, db,
        )
    except PublishServiceError as e:
        raise PermanentItemError(f"Could not start the YouTube upload: {e}") from e

    item.publish_job_id = job.id
    item.scheduled_publish_at = publish_at.astimezone(timezone.utc).replace(tzinfo=None) if publish_at else None
    item.youtube_url = None
    item.status = "publishing"
    db.commit()


def reconcile_publish_status(db, batches: Iterable[Batch]) -> bool:
    """Move 'publishing' items to published/failed from their PublishJob. Returns True if changed.

    In a hands-off batch a failed upload is retried automatically (a new
    upload of the same video), except when the account must be reconnected.
    """
    changed = False
    for batch in batches:
        for item in batch.items:
            if item.status != "publishing" or not item.publish_job_id:
                continue
            job = db.get(PublishJob, item.publish_job_id)
            if job is not None and job.status == "published":
                item.status = "published"
                item.youtube_url = job.platform_post_url or None
                item.error_message = None
                item.next_attempt_at = None
                item.finished_at = job.published_at or utcnow()
            elif job is None:
                fail_or_retry(batch, item,
                              "The YouTube upload job no longer exists. Retry to upload again.")
            elif job.status == "failed":
                fail_or_retry(batch, item,
                              f"YouTube upload failed: {job.error_message or 'unknown error'}",
                              retryable=job.error_code != "auth_error")
            elif job.status == "reconnect_required":
                fail_or_retry(batch, item, (
                    "YouTube upload failed: the account needs to be reconnected on the "
                    "Account page. Then retry this item."
                ), retryable=False)
            else:
                continue
            changed = True
    if changed:
        db.commit()
    return changed


def publish_wait_until(job: Optional[PublishJob]) -> Optional[datetime]:
    """Naive UTC time a queued upload is waiting for (quota reset or retry backoff), if any."""
    if job is None or job.status != "queued" or not job.next_retry_at:
        return None
    return job.next_retry_at


def quota_wait_step(job: Optional[PublishJob]) -> Optional[str]:
    """'Waiting for YouTube quota reset (resumes 09:05 your time)' for a job waiting on quota."""
    when = publish_wait_until(job)
    if when is None or job.error_code != "quota_exceeded":
        return None
    local = when.replace(tzinfo=timezone.utc).astimezone()  # the server's (= user's) clock
    return f"Waiting for YouTube quota reset (resumes {local:%H:%M} your time)"


# ── Topic brainstorming ───────────────────────────────────────


def _topic_planner(style: Optional[str] = None):
    from scene_planner import create_planner
    return create_planner(narration_style=style or settings.narration_style, provider=settings.llm_provider)


def existing_topics(db, user_id: int) -> List[str]:
    """Titles and topics the user already has (videos and batch items), to avoid repeating."""
    out: List[str] = []
    for (title,) in db.query(Video.title).filter(Video.user_id == user_id).all():
        if title:
            out.append(title)
    rows = (
        db.query(BatchItem.title, BatchItem.input_type, BatchItem.input_text)
        .join(Batch, Batch.id == BatchItem.batch_id)
        .filter(Batch.user_id == user_id)
        .all()
    )
    for title, input_type, text in rows:
        if title:
            out.append(title)
        if input_type == "prompt" and text:
            out.append(text)
    return out


def suggest_topics(db, user_id: int, niche: str, count: int, style: Optional[str] = None) -> List[str]:
    """``count`` fresh topics for a niche, avoiding everything the user already made or queued.

    Raises BatchError(502) when the LLM fails or returns nothing usable.
    """
    try:
        planner = _topic_planner(style)
        topics = planner.suggest_topics(niche, count, avoid=existing_topics(db, user_id), style=style)
    except Exception as e:
        logger.exception("Topic suggestion failed")
        detail = (str(e) or e.__class__.__name__).strip().splitlines()[0][:200]
        raise BatchError(f"Could not come up with topics right now ({detail}). Try again.",
                         status_code=502) from e
    if not topics:
        raise BatchError("The AI did not return any usable topics. Try again or reword the niche.",
                         status_code=502)
    return topics[:count]


# ── Recovery (called from the API's stale-job sweeper) ────────


def recover_stuck_batches(now: Optional[datetime] = None) -> dict:
    """Restart broken runner chains and fail items whose worker died.

    For each running batch with no live runner lock: items stuck in a runner
    stage for more than 2 minutes are marked failed, and if nothing is in
    progress and the batch has been idle for more than 2 minutes, the runner
    is re-queued.
    """
    now = now or utcnow()
    cutoff = now - timedelta(seconds=STALE_SECONDS)
    r = get_redis()
    db = SessionLocal()
    stats = {"failed_items": 0, "requeued": 0}
    try:
        reconcile_publish_status(
            db,
            db.query(Batch).filter(Batch.items.any(BatchItem.status == "publishing")).all(),
        )
        for batch in db.query(Batch).filter(Batch.status == "running").all():
            if runner_lock_held(r, batch.id):
                continue
            marked = False
            for item in batch.items:
                if _is_runner_active(item) and (item.started_at or datetime.min) < cutoff:
                    fail_or_retry(batch, item, "Worker stopped while processing", now=now)
                    marked = True
                    stats["failed_items"] += 1
            if marked:
                batch.updated_at = now
                db.commit()
            if any(_is_runner_active(i) for i in batch.items) or queued_runner_job(r, batch.id):
                continue
            if marked or (batch.updated_at or datetime.min) < cutoff:
                enqueue_runner(batch.id, r)
                stats["requeued"] += 1
                logger.warning("Batch %s: re-queued runner (no live runner found)", batch.id)
    finally:
        db.close()
    return stats


# ── Batch operations used by the API ──────────────────────────


def counts_for(items: Iterable[BatchItem]) -> dict:
    counts = {"pending": 0, "in_progress": 0, "done": 0, "published": 0, "failed": 0, "cancelled": 0}
    for item in items:
        if item.status in IN_PROGRESS_STATUSES:
            counts["in_progress"] += 1
        elif item.status in counts:
            counts[item.status] += 1
    return counts


def add_items(batch: Batch, items: List[dict]) -> None:
    existing = list(batch.items)
    if len(existing) + len(items) > MAX_ITEMS:
        raise BatchError(
            f"A batch can hold at most {MAX_ITEMS} videos (this one has {len(existing)})."
        )
    position = max((i.position for i in existing), default=-1) + 1
    for data in items:
        batch.items.append(BatchItem(
            position=position,
            input_type=data["input_type"],
            input_text=data["text"],
            title=data.get("title") or None,
            status=PENDING,
            attempts=0,
        ))
        position += 1


def _start_runner_or_revert(db, batch: Batch, previous_status: str) -> None:
    try:
        enqueue_runner(batch.id)
    except Exception as e:
        logger.exception("Could not queue batch %s", batch.id)
        batch.status = previous_status
        db.commit()
        raise BatchError(
            "Could not reach the job queue. Make sure Redis and the worker are running.",
            status_code=503,
        ) from e


def start_batch(db, batch: Batch) -> None:
    if batch.status == "running":
        _start_runner_or_revert(db, batch, "running")  # idempotent: revive a dead chain
        return
    if not any(i.status == PENDING for i in batch.items):
        raise BatchError("Nothing left to run. Add videos or retry failed ones first.")
    previous = batch.status
    batch.status = "running"
    batch.updated_at = utcnow()
    db.commit()
    _start_runner_or_revert(db, batch, previous)


def pause_batch(db, batch: Batch) -> None:
    if batch.status == "paused":
        return
    if batch.status != "running":
        raise BatchError("Only a running batch can be paused.")
    batch.status = "paused"
    batch.updated_at = utcnow()
    db.commit()


def cancel_batch(db, batch: Batch) -> None:
    if batch.status == "cancelled":
        return
    if batch.status == "completed":
        raise BatchError("This batch has already finished.")
    now = utcnow()
    batch.status = "cancelled"
    batch.updated_at = now
    for item in batch.items:
        if item.status == PENDING:
            item.status = "cancelled"
            item.finished_at = now
    db.commit()


def retry_item(db, batch: Batch, item: BatchItem) -> None:
    if item.status not in ("failed", "cancelled"):
        raise BatchError("Only failed or cancelled videos can be retried.")
    item.status = PENDING
    item.error_message = None
    item.next_attempt_at = None
    item.started_at = None
    item.finished_at = None
    item.scheduled_publish_at = None
    item.youtube_url = None
    if batch.status in ("completed", "cancelled"):
        batch.status = "paused"
    batch.updated_at = utcnow()
    db.commit()


def delete_item(db, batch: Batch, item: BatchItem) -> None:
    if item.status not in (PENDING, "failed", "cancelled"):
        raise BatchError("Only pending, failed or cancelled videos can be removed.")
    batch.items.remove(item)
    db.delete(item)
    batch.updated_at = utcnow()
    db.commit()


def delete_batch(db, batch: Batch) -> None:
    if any(_is_runner_active(i) for i in batch.items):
        raise BatchError(
            "A video in this batch is being made right now. Pause or cancel the batch, "
            "wait for that video to finish, then delete it.",
            status_code=409,
        )
    db.delete(batch)
    db.commit()
