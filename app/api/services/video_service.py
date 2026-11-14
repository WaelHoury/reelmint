"""Video generation service for API."""

import json
import logging
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional
from rq import Queue
from redis import Redis

from app.core.config import settings, get_words_per_second
from app.api.services.session_store import VideoSession, session_store, ClipInfo
from app.utils.helpers import sanitize_filename

logger = logging.getLogger(__name__)


def can_start_generation(session: VideoSession) -> bool:
    """Clips confirmed, or a failed render that still has its clips (retry)."""
    if session.status == "clips_ready":
        return True
    return session.status == "failed" and bool(session.clips)


class VideoService:
    """Handles video generation business logic."""

    USER_GEN_LOCK_PREFIX = "user_generating:"
    SESSION_ENQUEUE_LOCK_PREFIX = "session_enqueue:"

    def __init__(self):
        """Initialize video service."""
        self.redis = Redis.from_url(settings.redis_url)
        self.queue = Queue('video_generation', connection=self.redis)

    # --- Per-user generation lock ---

    def _acquire_user_generation_lock(self, user_id: int, session_id: str, ttl: int = 2700) -> bool:
        """Acquire per-user generation lock. Returns True if acquired.

        TTL is 45 minutes (matches job_timeout) so the lock auto-expires
        even if the worker crashes without releasing it.
        """
        key = f"{self.USER_GEN_LOCK_PREFIX}{user_id}"
        return bool(self.redis.set(key, session_id, nx=True, ex=ttl))

    def _release_user_generation_lock(self, user_id: int, session_id: str):
        """Release per-user lock only if it belongs to this session (atomic Lua check-and-delete)."""
        key = f"{self.USER_GEN_LOCK_PREFIX}{user_id}"
        lua = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        self.redis.eval(lua, 1, key, session_id)

    def _get_active_generation_session(self, user_id: int) -> Optional[str]:
        """Get the session_id currently generating for this user, or None."""
        key = f"{self.USER_GEN_LOCK_PREFIX}{user_id}"
        val = self.redis.get(key)
        return val.decode() if val else None

    def _acquire_session_enqueue_lock(self, session_id: str, ttl: int = 60) -> bool:
        """Prevent duplicate enqueue for the same session (double-click / refresh race)."""
        key = f"{self.SESSION_ENQUEUE_LOCK_PREFIX}{session_id}"
        return bool(self.redis.set(key, "1", nx=True, ex=ttl))

    def generate_script(self, session: VideoSession) -> VideoSession:
        """Generate narration script from prompt.

        Args:
            session: Session with prompt data

        Returns:
            Updated session with generated script
        """
        if session.input_type == "script":
            # Script already provided, just update word count
            session.word_count = len(session.script_text.split())
            wps = get_words_per_second(provider=session.tts_provider)
            session.estimated_duration_seconds = session.word_count / wps
            session.status = "draft"
            session_store.update(session)
            return session

        # Generate script from prompt using LLM
        from scene_planner import create_planner

        style = session.narration_style or settings.narration_style
        planner = create_planner(narration_style=style, provider=settings.llm_provider)

        duration_minutes = session.duration_seconds / 60
        wps = get_words_per_second(provider=session.tts_provider)

        # Generate narration only (clips are generated separately in generate_clips_task)
        narration = planner.generate_narration(
            prompt=session.prompt,
            duration_minutes=duration_minutes,
            words_per_second=wps,
            loop_mode=session.loop_mode,
        )

        session.script_text = narration
        session.word_count = len(narration.split())
        session.estimated_duration_seconds = session.word_count / wps
        session.status = "draft"
        session_store.update(session)
        return session

    def update_script(self, session: VideoSession, script_text: str) -> VideoSession:
        """Update session script.

        Args:
            session: Session to update
            script_text: New script text

        Returns:
            Updated session
        """
        session.script_text = script_text
        session.word_count = len(script_text.split())
        wps = get_words_per_second(provider=session.tts_provider)
        session.estimated_duration_seconds = session.word_count / wps
        session_store.update(session)
        return session

    def confirm_script(self, session: VideoSession) -> VideoSession:
        """Confirm script is ready, advance to next stage.

        Args:
            session: Session to update

        Returns:
            Updated session
        """
        if not session.script_text:
            raise ValueError("No script to confirm")
        session.status = "script_ready"
        session_store.update(session)
        return session

    def generate_clips(self, session: VideoSession) -> VideoSession:
        """Run clip generation in a background thread on the API server.

        Clip search is I/O-bound (LLM + Pexels API calls), so it runs on
        the API server instead of consuming a heavy RQ worker. This keeps
        all workers free for video generation (the CPU-intensive task).
        """
        if session.status not in ("script_ready", "clips_generating"):
            raise ValueError("Script must be confirmed before generating clips")

        # If already generating, return current state (idempotent)
        if session.status == "clips_generating":
            return session

        session.status = "clips_generating"
        session.clips = []  # will be populated progressively
        session.clips_started_at = time.time()
        session_store.update(session)

        # Run in background thread (I/O-bound, no heavy CPU)
        import threading
        thread = threading.Thread(
            target=generate_clips_task,
            args=(session.session_id,),
            daemon=True,
        )
        thread.start()

        logger.info("Clip generation started on API server for session %s", session.session_id)
        return session

    def update_clip(self, session: VideoSession, clip_index: int,
                    visual_description: str, keywords: Optional[List[str]] = None) -> VideoSession:
        """Update a single clip and search for new video.

        Args:
            session: Session with clips
            clip_index: Index of clip to update
            visual_description: New visual description
            keywords: Optional new keywords (extracted from description if not provided)

        Returns:
            Updated session
        """
        if clip_index >= len(session.clips):
            raise ValueError(f"Invalid clip index: {clip_index}")

        from video_searcher import CachedVideoSearcher
        from app.media.multi_source_searcher import MultiSourceSearcher

        # Extract keywords from description if not provided
        if keywords is None:
            words = visual_description.lower().split()
            stopwords = {'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'and', 'or', 'is', 'are', 'was', 'were'}
            keywords = [w for w in words if w not in stopwords and len(w) > 2][:5]

        # Search for new video (with cache)
        redis_client = Redis.from_url(settings.redis_url)
        pexels_searcher = CachedVideoSearcher(
            api_key=settings.pexels_api_key,
            redis_client=redis_client,
            cache_ttl=settings.clip_search_cache_ttl,
        )

        pixabay_searcher = None
        if (settings.enable_pixabay or settings.enable_image_fallback) and settings.pixabay_api_key:
            from app.media.pixabay_search import CachedPixabaySearcher, PIXABAY_CACHE_TTL
            pixabay_searcher = CachedPixabaySearcher(
                api_key=settings.pixabay_api_key,
                redis_client=redis_client,
                cache_ttl=getattr(settings, 'pixabay_cache_ttl', PIXABAY_CACHE_TTL),
            )

        searcher = MultiSourceSearcher(
            pexels_searcher=pexels_searcher,
            pixabay_searcher=pixabay_searcher,
            enable_pixabay=settings.enable_pixabay,
            enable_image_fallback=settings.enable_image_fallback,
        )
        # Never pick a clip the video's other shots already use (same broadening
        # ladder, incl. the still-image fallback, as initial clip generation).
        from app.media.clip_picker import ClipReservations, clip_key, pick_clip
        others = ClipReservations(
            clip_key(c) for j, c in enumerate(session.clips or []) if j != clip_index
        )
        media_info, _rung = pick_clip(
            searcher,
            {'keywords': keywords, 'visual_description': visual_description},
            session.orientation,
            min_duration=_shot_seconds(session, len(session.clips or [])),
            reservations=others,
            topic=session.prompt or session.title,
            label=f"#{clip_index}",
        )

        media_type = getattr(media_info, 'media_type', 'video') if media_info else None
        session.clips[clip_index] = {
            'index': clip_index,
            'keywords': keywords,
            'visual_description': visual_description,
            'video_preview_url': media_info.preview_url if media_info else None,
            'video_id': str(media_info.id) if media_info else None,
            'author': media_info.author if media_info else None,
            'author_url': media_info.url if media_info else None,
            'duration_seconds': (media_info.duration or None) if media_info and media_type != 'image' else None,
            'search_status': 'ready' if media_info else 'failed',
            'search_error': None if media_info else 'No matching videos found',
            'media_type': media_type,
            'provider': getattr(media_info, 'provider', 'Pexels') if media_info else None,
        }

        session_store.update(session)
        return session

    def confirm_clips(self, session: VideoSession) -> VideoSession:
        """Confirm all clips are ready.

        Args:
            session: Session to update

        Returns:
            Updated session
        """
        if not session.clips:
            raise ValueError("No clips to confirm")
        session.status = "clips_ready"
        session_store.update(session)
        return session

    def queue_video_generation(self, session: VideoSession, is_superuser: bool = False) -> str:
        """Queue video generation as background job.

        Args:
            session: Session ready for generation
            is_superuser: If True, skip per-user concurrency limit

        Returns:
            Job ID

        Raises:
            ValueError: If session not ready, or user already has an active generation
        """
        if not can_start_generation(session):
            raise ValueError("Clips must be confirmed before generating video")

        # Per-session dedup lock (prevents double-click / refresh race)
        if not self._acquire_session_enqueue_lock(session.session_id):
            # Already being enqueued — return existing job_id if available
            if session.job_id:
                return session.job_id
            raise ValueError("Generation already being queued for this session")

        # Per-user concurrency lock (superuser exempt)
        if not is_superuser and session.user_id:
            existing = self._get_active_generation_session(session.user_id)
            if existing and existing != session.session_id:
                raise ValueError(
                    "You already have a video generating. "
                    "Please wait for it to finish before starting another."
                )
            if not self._acquire_user_generation_lock(session.user_id, session.session_id):
                raise ValueError(
                    "You already have a video generating. "
                    "Please wait for it to finish before starting another."
                )

        previous_status = session.status
        previous_error = session.error_message
        session.status = "generating"
        if previous_status == "failed":
            # Retrying a failed render: start from a clean slate
            session.error_message = None
            session.progress_percent = 0
            session.current_step = "Queued for processing..."
            session.job_id = None
        session_store.update(session)

        # Queue the job. If enqueue fails (e.g. Redis down), undo the status
        # change and release the locks so the user is not locked out.
        try:
            job = self.queue.enqueue(
                'app.api.services.video_service.generate_video_task',
                session.session_id,
                job_timeout='45m',
            )
        except Exception:
            logger.exception("Failed to enqueue video generation for session %s", session.session_id)
            session.status = previous_status
            session.error_message = previous_error
            try:
                session_store.update(session)
            except Exception:
                logger.exception("Failed to roll back status for session %s", session.session_id)
            if not is_superuser and session.user_id:
                try:
                    self._release_user_generation_lock(session.user_id, session.session_id)
                except Exception:
                    logger.exception("Failed to release user lock for session %s", session.session_id)
            try:
                self.redis.delete(f"{self.SESSION_ENQUEUE_LOCK_PREFIX}{session.session_id}")
            except Exception:
                pass
            raise

        session.job_id = job.id
        session_store.update(session)
        return job.id

    @staticmethod
    def _map_rq_status(rq_status: str, session_status: str = None) -> str:
        """Map RQ job status to API schema status."""
        mapping = {
            "queued": "queued", "deferred": "queued", "scheduled": "queued",
            "started": "processing",
            "finished": "completed",
            "failed": "failed", "stopped": "failed", "canceled": "failed",
        }
        result = mapping.get(rq_status)
        if result:
            return result
        # Fall back to session status
        session_mapping = {"completed": "completed", "failed": "failed", "generating": "processing"}
        return session_mapping.get(session_status, "queued")

    def get_job_status(self, session: VideoSession) -> Dict:
        """Get video generation job status.

        Args:
            session: Session with job

        Returns:
            Job status dict with mapped status values
        """
        if not session.job_id:
            # No job enqueued yet. JobStatusResponse only allows
            # queued|processing|completed|failed, so map the session status.
            # Pre-generation statuses (draft, script_ready, clips_ready, ...)
            # map to "queued" with job_id=None: the frontend only resumes
            # polling when a job_id is present, otherwise it POSTs /generate.
            no_job_mapping = {
                "generating": "processing",
                "completed": "completed",
                "failed": "failed",
            }
            mapped = no_job_mapping.get(session.status, "queued")
            return {
                'status': mapped,
                'job_id': None,
                'error_message': session.error_message if mapped == 'failed' else None,
            }

        from rq.job import Job
        try:
            job = Job.fetch(session.job_id, connection=self.redis)
            raw_status = job.get_status()
            mapped_status = self._map_rq_status(raw_status, session.status)

            result = {
                'job_id': session.job_id,
                'status': mapped_status,
                'progress_percent': session.progress_percent,
                'current_step': session.current_step,
                'error_message': session.error_message if mapped_status == 'failed' else None,
                'output_url': f"/api/sessions/{session.session_id}/video" if mapped_status == 'completed' else None,
            }

            if mapped_status == 'queued':
                queue = Queue('video_generation', connection=self.redis)
                job_ids = queue.job_ids
                if session.job_id in job_ids:
                    result['queue_position'] = job_ids.index(session.job_id) + 1
                result['queue_length'] = len(job_ids)

            return result
        except Exception:
            return {
                'job_id': session.job_id,
                'status': self._map_rq_status(session.status, session.status),
                'error_message': session.error_message,
            }


def _shot_seconds(session, clip_count: int) -> float:
    """Roughly how long each shot is on screen (clips at least this long are preferred)."""
    total = session.estimated_duration_seconds or session.duration_seconds or 0
    if not total or not clip_count:
        return 0.0
    transition = float(os.getenv("TRANSITION_DURATION", "0.5"))
    return (float(total) + max(0, clip_count - 1) * transition) / clip_count


def generate_clips_task(session_id: str):
    """Background task: LLM clip generation + parallel Pexels search with progressive updates.

    Runs in a background thread on the API server. All errors are caught and
    logged — the thread must never raise, as unhandled exceptions in threads
    are silently swallowed in Docker.
    """
    try:
        logger.info("Clip generation thread started for session %s", session_id)

        session = session_store.get(session_id)
        if not session:
            logger.error("Clip generation: session not found: %s", session_id)
            return

        t_total = time.time()

        from scene_planner import create_planner
        from video_searcher import CachedVideoSearcher
        from app.media.multi_source_searcher import MultiSourceSearcher
        from app.media.clip_picker import ClipReservations, pick_clip

        # --- Stage 1: LLM clip descriptions ---
        t_llm = time.time()
        planner = create_planner(narration_style=settings.narration_style, provider=settings.llm_provider)
        duration_minutes = session.duration_seconds / 60
        plan = planner.generate_clips_from_script(session.script_text, duration_minutes)
        llm_elapsed = time.time() - t_llm
        logger.info("Session %s: LLM clip generation took %.1fs (%d clips)",
                     session_id, llm_elapsed, len(plan.clips))

        # Initialize placeholder clips in session
        session.clips = [
            {
                'index': i,
                'keywords': clip.keywords,
                'visual_description': clip.visual_description,
                'video_preview_url': None,
                'video_id': None,
                'author': None,
                'author_url': None,
                'duration_seconds': None,
                'search_status': 'pending',
            }
            for i, clip in enumerate(plan.clips)
        ]
        session_store.update(session)

        # --- Stage 2: Parallel media searches ---
        t_search = time.time()
        redis_client = Redis.from_url(settings.redis_url)
        pexels_searcher = CachedVideoSearcher(
            api_key=settings.pexels_api_key,
            redis_client=redis_client,
            cache_ttl=settings.clip_search_cache_ttl,
        )

        pixabay_searcher = None
        if (settings.enable_pixabay or settings.enable_image_fallback) and settings.pixabay_api_key:
            from app.media.pixabay_search import CachedPixabaySearcher, PIXABAY_CACHE_TTL
            pixabay_searcher = CachedPixabaySearcher(
                api_key=settings.pixabay_api_key,
                redis_client=redis_client,
                cache_ttl=getattr(settings, 'pixabay_cache_ttl', PIXABAY_CACHE_TTL),
            )

        searcher = MultiSourceSearcher(
            pexels_searcher=pexels_searcher,
            pixabay_searcher=pixabay_searcher,
            enable_pixabay=settings.enable_pixabay,
            enable_image_fallback=settings.enable_image_fallback,
        )

        total_clips = len(plan.clips)
        completed = 0

        # Clips already picked for this video's shots. Parallel searches
        # reserve their pick atomically, so no two shots get the same clip.
        reservations = ClipReservations()
        shot_seconds = _shot_seconds(session, total_clips)
        topic = session.prompt or session.title

        def _search_one(idx, clip):
            """Search for a single clip. Returns (index, clip_dict)."""
            try:
                media_info, rung = pick_clip(
                    searcher, clip, session.orientation,
                    min_duration=shot_seconds,
                    reservations=reservations,
                    topic=topic,
                    label=f"{session_id[:8]}#{idx}",
                )
                media_type = (getattr(media_info, 'media_type', None) or 'video') if media_info else None
                if media_info and rung not in (None, 'full_keywords'):
                    logger.info("Session %s clip %d: used rung '%s'", session_id, idx, rung)
                if media_type == 'image':
                    return idx, {
                        'index': idx,
                        'keywords': clip.keywords,
                        'visual_description': clip.visual_description,
                        'video_preview_url': media_info.preview_url,
                        'video_id': str(media_info.id),
                        'author': media_info.author,
                        'author_url': media_info.url,
                        'duration_seconds': None,
                        'search_status': 'ready',
                        'search_error': None,
                        'media_type': 'image',
                        'provider': getattr(media_info, 'provider', 'Pixabay'),
                    }

                video_info = media_info
                return idx, {
                    'index': idx,
                    'keywords': clip.keywords,
                    'visual_description': clip.visual_description,
                    'video_preview_url': video_info.preview_url if video_info else None,
                    'video_id': str(video_info.id) if video_info else None,
                    'author': video_info.author if video_info else None,
                    'author_url': video_info.url if video_info else None,
                    'duration_seconds': video_info.duration if video_info else None,
                    'search_status': 'ready' if video_info else 'failed',
                    'search_error': None if video_info else 'No matching videos found',
                    'media_type': 'video' if video_info else None,
                    'provider': getattr(video_info, 'provider', 'Pexels') if video_info else None,
                }
            except Exception as e:
                logger.warning("Clip %d search failed: %s", idx, e)
                return idx, {
                    'index': idx,
                    'keywords': clip.keywords,
                    'visual_description': clip.visual_description,
                    'video_preview_url': None,
                    'video_id': None,
                    'author': None,
                    'author_url': None,
                    'duration_seconds': None,
                    'search_status': 'failed',
                    'search_error': str(e),
                }

        workers = min(settings.clip_search_workers, total_clips)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_search_one, i, clip): i for i, clip in enumerate(plan.clips)}
            for future in as_completed(futures):
                idx, clip_dict = future.result()
                # Write result back by index (preserves ordering)
                session = session_store.get(session_id)  # re-read to avoid stale overwrites
                if session and idx < len(session.clips):
                    session.clips[idx] = clip_dict
                    session_store.update(session)
                completed += 1

        search_elapsed = time.time() - t_search
        total_elapsed = time.time() - t_total

        # Finalize
        session = session_store.get(session_id)
        session.status = "clips_ready"
        session_store.update(session)

        stats = getattr(searcher, 'cache_stats', None) or {'hits': 0, 'misses': 0, 'hit_rate': 0.0}
        logger.info(
            "Session %s clip generation complete: %.1fs total (LLM: %.1fs, search: %.1fs), "
            "%d clips, cache hit rate: %.0f%% (%d hits, %d misses)",
            session_id, total_elapsed, llm_elapsed, search_elapsed,
            total_clips, stats['hit_rate'] * 100, stats['hits'], stats['misses'],
        )

    except Exception as e:
        logger.exception("Clip generation failed for session %s", session_id)
        try:
            session = session_store.get(session_id)
            if session:
                session.status = "script_ready"  # allow retry
                session.error_message = f"Clip generation failed: {e}"
                session_store.update(session)
        except Exception:
            logger.exception("Failed to update session %s after clip generation error", session_id)


def _release_generation_lock(session: VideoSession):
    """Release per-user generation lock for a session. Safe to call multiple times."""
    if not session.user_id:
        return
    try:
        redis_conn = Redis.from_url(settings.redis_url)
        key = f"{VideoService.USER_GEN_LOCK_PREFIX}{session.user_id}"
        lua = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        redis_conn.eval(lua, 1, key, session.session_id)
    except Exception:
        logger.warning("Failed to release generation lock for user %s", session.user_id)


def _subtitle_overrides(session) -> Optional[dict]:
    """Caption font/size/colors/position chosen for this video, if any."""
    overrides = {
        'font': session.subtitle_font,
        'font_size': session.subtitle_font_size,
        'color': session.subtitle_color,
        'outline_color': session.subtitle_outline_color,
        'position': session.subtitle_position,
    }
    overrides = {k: v for k, v in overrides.items() if v}
    return overrides or None


def generate_video_task(session_id: str):
    """Background task for video generation.

    Args:
        session_id: Session ID to generate video for
    """
    session = session_store.get(session_id)
    if not session:
        raise ValueError(f"Session not found: {session_id}")

    # Heartbeat Redis connection (reused across progress callbacks)
    heartbeat_redis = Redis.from_url(settings.redis_url)
    heartbeat_key = f"job_heartbeat:{session_id}"

    try:
        from generate_video import generate_video
        from pathlib import Path

        # Set initial heartbeat
        heartbeat_redis.setex(heartbeat_key, 120, "alive")

        # Update progress
        session.current_step = "Starting video generation..."
        session.progress_percent = 5
        session_store.update(session)

        # Progress callback that writes to Redis so the frontend can poll
        def _update_progress(percent, step):
            session.progress_percent = percent
            session.current_step = step
            session_store.update(session)
            # Refresh heartbeat so stale detector knows we're alive
            heartbeat_redis.setex(heartbeat_key, 120, "alive")
            # Extend user generation lock TTL
            if session.user_id:
                lock_key = f"{VideoService.USER_GEN_LOCK_PREFIX}{session.user_id}"
                heartbeat_redis.expire(lock_key, 2700)

        # Intermediate callback to save file paths for editor and subtitle re-generation
        def _save_intermediates(data):
            session.temp_dir_path = data.get('temp_dir')
            session.video_no_audio_path = data.get('video_no_audio')
            session.final_audio_path = data.get('final_audio')
            session.narration_audio_path = data.get('narration_audio')
            session.music_file_path = data.get('music_file')
            session.word_timestamps = data.get('word_timestamps')
            session.trimmed_clip_paths = data.get('trimmed_clips')
            session.clip_duration = data.get('clip_duration')
            session.transition_duration = data.get('transition_duration')
            session.render_info = data.get('render')
            session_store.update(session)

        # Generate video using existing function
        safe_title = sanitize_filename(session.title)
        filename = f"u{session.user_id}_{session.session_id[:8]}_{safe_title}.mp4"
        output_path = Path(settings.output_dir) / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build tts_selection dict from session fields
        tts_sel = None
        if session.tts_voice_key:
            tts_sel = {
                "provider": session.tts_provider,
                "voice_key": session.tts_voice_key,
                "label": session.tts_voice_label,
            }

        generate_video(
            prompt=session.prompt,
            duration_minutes=session.duration_seconds / 60,
            output_path=output_path,
            orientation=session.orientation,
            script_text=session.script_text,
            keep_temp=True,  # Keep temp files for subtitle re-generation
            progress_callback=_update_progress,
            narration_style=session.narration_style,
            intermediate_callback=_save_intermediates,
            pre_selected_clips=session.clips if session.clips else None,
            tts_selection=tts_sel,
            subtitles_enabled=session.subtitles_enabled,
            subtitle_style=session.subtitle_style,
            subtitle_settings=_subtitle_overrides(session),
            loop_mode=session.loop_mode,
        )

        session.status = "completed"
        session.output_path = str(output_path)
        credits_txt = output_path.parent / f"{output_path.stem}_credits.txt"
        session.credits_path = str(credits_txt) if credits_txt.exists() else None
        session.progress_percent = 100
        session.current_step = "Complete!"
        session_store.update(session)

        # Release per-user generation lock and clean up heartbeat
        _release_generation_lock(session)
        heartbeat_redis.delete(heartbeat_key)

        # Save completed video to PostgreSQL if user is authenticated
        if session.user_id:
            video_id = _save_video_to_db(session, output_path)

            # Persist intermediate files for the video editor
            if video_id:
                project_dir = _persist_project_files(session, output_path, video_id)
                if project_dir:
                    # Update session with persistent paths
                    session.project_dir_path = str(project_dir)
                    # video_no_audio is generated on-demand when editor opens
                    vna_path = project_dir / "video_no_audio.mp4"
                    session.video_no_audio_path = str(vna_path) if vna_path.exists() else None
                    session.final_audio_path = str(project_dir / "audio_final.wav")
                    session.narration_audio_path = str(project_dir / "narration.wav")
                    session_store.update(session)

                # Upload to R2 if enabled (after local project files are persisted)
                if settings.use_r2_storage:
                    _update_progress(95, "Uploading to cloud storage...")
                    try:
                        _upload_to_r2(session, output_path, video_id, project_dir)
                    except Exception:
                        logger.exception("R2 upload failed for video %d", video_id)
                        session.status = "failed"
                        session.error_message = "Cloud storage upload failed. Please try again."
                        session_store.update(session)
                        _release_generation_lock(session)
                        heartbeat_redis.delete(heartbeat_key)
                        raise

    except Exception as e:
        logger.exception("Video generation failed for session %s", session_id)
        session.status = "failed"
        session.error_message = "Video generation failed. Check server logs for details."
        session_store.update(session)
        # Release per-user generation lock and clean up heartbeat
        _release_generation_lock(session)
        heartbeat_redis.delete(heartbeat_key)
        raise


def _upload_to_r2(session, output_path: Path, video_id: int, project_dir):
    """Upload final video, thumbnail, and project files to R2.

    Updates the Video DB record with R2 keys.
    In production, removes local persistent copies after a successful upload.
    Raises on any failure — caller must mark the job as failed.
    """
    from app.api.services.r2_storage import (
        r2_storage, r2_video_key, r2_thumbnail_key,
        r2_project_prefix,
    )
    from app.db.base import SessionLocal
    from app.db.models import Video

    user_id = session.user_id
    output_path = Path(output_path)

    # Upload final video
    video_key = r2_video_key(user_id, video_id, version=1)
    r2_storage.upload_file(output_path, video_key)

    # Upload thumbnail (same name, .jpg extension)
    thumb_key = None
    thumb_path = output_path.with_suffix(".jpg")
    if thumb_path.exists():
        thumb_key = r2_thumbnail_key(user_id, video_id)
        r2_storage.upload_file(thumb_path, thumb_key)

    # Upload entire project directory
    proj_prefix = None
    if project_dir and Path(project_dir).exists():
        proj_prefix = r2_project_prefix(user_id, video_id)
        r2_storage.upload_directory(Path(project_dir), proj_prefix)

    # Persist R2 keys to DB
    db = SessionLocal()
    try:
        video = db.query(Video).filter(Video.id == video_id).first()
        if video:
            video.r2_video_key = video_key
            video.r2_thumbnail_key = thumb_key
            video.r2_project_prefix = proj_prefix
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # Production: clean up local persistent copies
    if settings.environment == "production":
        _cleanup_local_after_r2_upload(output_path, thumb_path, project_dir)

    logger.info("R2 upload complete for video %d (user %d)", video_id, user_id)


def _cleanup_local_after_r2_upload(output_path: Path, thumb_path, project_dir):
    """Remove local persistent files after a successful R2 upload.

    Only called in production. Development keeps local files for debugging.
    Failures are non-fatal (logged as warnings).
    """
    import shutil
    try:
        if output_path and Path(output_path).exists():
            Path(output_path).unlink()
        if thumb_path and Path(thumb_path).exists():
            Path(thumb_path).unlink()
        if project_dir and Path(project_dir).exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        logger.info("Cleaned up local files after R2 upload: %s", output_path)
    except Exception as e:
        logger.warning("Local cleanup after R2 upload encountered an error: %s", e)


def _find_project_dir(session):
    """Find the project directory for a session, checking session field, DB, and R2."""
    if session.project_dir_path:
        project_dir = Path(session.project_dir_path)
        if project_dir.exists():
            return project_dir

    # Try to find via DB video record
    if session.user_id:
        from app.db.base import SessionLocal
        from app.db.models import Video
        db = SessionLocal()
        try:
            output_filename = Path(session.output_path).name if session.output_path else ""
            video = db.query(Video).filter(
                Video.user_id == session.user_id,
                Video.output_filename == output_filename,
            ).first()
            if video:
                if video.project_data_path:
                    project_dir = Path(video.project_data_path)
                    if project_dir.exists():
                        return project_dir
                # R2 fallback: download project files on demand
                if settings.use_r2_storage and video.r2_project_prefix:
                    try:
                        from app.api.services.r2_storage import r2_storage
                        project_dir = Path(settings.projects_dir) / str(video.id)
                        r2_storage.download_directory(video.r2_project_prefix, project_dir)
                        if (project_dir / "project.json").exists():
                            # Update DB path for future requests
                            video.project_data_path = str(project_dir)
                            db.commit()
                            return project_dir
                    except Exception as e:
                        logger.warning("R2 project download failed for session %s: %s", session.session_id, e)
        except Exception:
            pass
        finally:
            db.close()

    return None


def build_media_searcher():
    """Pexels (+ Pixabay when enabled) searcher with the Redis result cache."""
    from video_searcher import CachedVideoSearcher
    from app.media.multi_source_searcher import MultiSourceSearcher

    redis_client = Redis.from_url(settings.redis_url)
    pexels_searcher = CachedVideoSearcher(
        api_key=settings.pexels_api_key,
        redis_client=redis_client,
        cache_ttl=settings.clip_search_cache_ttl,
    )
    pixabay_searcher = None
    if (settings.enable_pixabay or settings.enable_image_fallback) and settings.pixabay_api_key:
        from app.media.pixabay_search import CachedPixabaySearcher, PIXABAY_CACHE_TTL
        pixabay_searcher = CachedPixabaySearcher(
            api_key=settings.pixabay_api_key,
            redis_client=redis_client,
            cache_ttl=getattr(settings, 'pixabay_cache_ttl', PIXABAY_CACHE_TTL),
        )
    return MultiSourceSearcher(
        pexels_searcher=pexels_searcher,
        pixabay_searcher=pixabay_searcher,
        enable_pixabay=settings.enable_pixabay,
        enable_image_fallback=settings.enable_image_fallback,
    )


def normalize_subtitle_settings(subtitle_settings: dict) -> dict:
    """Validate/normalize wizard subtitle settings (colours -> #RRGGBB).

    Raises:
        ValueError: with a message fit for the user (the route returns 400).
    """
    from subtitle_generator import SubtitleGenerator

    out = dict(subtitle_settings or {})
    for key in ('color', 'outline_color'):
        if out.get(key):
            try:
                out[key] = SubtitleGenerator.normalize_color(out[key])
            except ValueError:
                label = 'Text colour' if key == 'color' else 'Outline colour'
                raise ValueError(f"{label} {out[key]!r} is not a colour; use #RRGGBB")
    if out.get('position') and out['position'] not in ('bottom', 'center', 'top'):
        raise ValueError("Position must be bottom, center or top")
    if out.get('font') and not re.fullmatch(r"[A-Za-z0-9 _.\-]{1,64}", out['font']):
        raise ValueError("Font name may only contain letters, digits, spaces, '-', '_' and '.'")
    return out


def regenerate_subtitles(session: VideoSession, subtitle_settings: dict) -> VideoSession:
    """Re-generate subtitles with custom settings without re-running the full pipeline.

    Uses the persisted project (projects/<video_id>/) and the editor's render
    path, so the result matches the first render (outro, loop, transitions,
    audio padding) and project.json stays in sync with the editor. The caller
    holds the video's edit lock.

    Args:
        session: Completed session
        subtitle_settings: Dict with keys: style, font, font_size, color, outline_color, position

    Returns:
        Updated session
    """
    if session.status != "completed":
        raise ValueError("Video must be completed before editing subtitles")
    if not session.output_path:
        raise ValueError("This video has no output file to update")

    subtitle_settings = normalize_subtitle_settings(subtitle_settings)

    project_dir = _find_project_dir(session)
    if not project_dir or not (Path(project_dir) / "project.json").exists():
        raise ValueError("Project files for this video are no longer available, so subtitles can't be re-generated")

    from app.api.services.editor_service import editor_service, EditorValidationError

    project_dir = Path(project_dir)
    project = editor_service.load_project(project_dir)
    # No style in the request = keep the one the video already has
    style = subtitle_settings.get('style') or session.subtitle_style \
        or project.get('subtitles', {}).get('style') or os.getenv("SUBTITLE_STYLE", "youtube")
    render_settings = {
        'enabled': True,
        'style': style,
        **{k: subtitle_settings.get(k) for k in ('font', 'font_size', 'color', 'outline_color', 'position')},
    }
    try:
        editor_service.render_changes(
            project_dir, project,
            subtitles=render_settings,
            output_path=Path(session.output_path),
        )
    except EditorValidationError as e:
        raise ValueError(str(e))

    # Save subtitle settings to session
    session.subtitles_enabled = True
    session.subtitle_style = style
    session.subtitle_font = subtitle_settings.get('font')
    session.subtitle_font_size = subtitle_settings.get('font_size')
    session.subtitle_color = subtitle_settings.get('color')
    session.subtitle_outline_color = subtitle_settings.get('outline_color')
    session.subtitle_position = subtitle_settings.get('position')
    session.project_dir_path = str(project_dir)
    session_store.update(session)

    return session


def _save_video_to_db(session, output_path):
    """Save completed video metadata to PostgreSQL and generate thumbnail.

    Returns:
        Video ID (int) or None if save failed.
    """
    from pathlib import Path
    import subprocess

    from app.db.base import SessionLocal
    from app.db.models import Video

    output_path = Path(output_path)
    file_size = output_path.stat().st_size if output_path.exists() else None

    # Generate thumbnail
    thumb_filename = None
    try:
        thumb_path = output_path.with_suffix(".jpg")
        subprocess.run(
            [
                settings.ffmpeg_path, "-i", str(output_path),
                "-ss", "2", "-vframes", "1", "-vf", "scale=480:-1",
                str(thumb_path), "-y",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
        )
        if thumb_path.exists():
            thumb_filename = thumb_path.name
    except Exception:
        pass

    db = SessionLocal()
    try:
        video = Video(
            user_id=session.user_id,
            title=session.title,
            status="completed",
            input_type=session.input_type,
            prompt=session.prompt,
            script_text=session.script_text,
            narration_style=session.narration_style,
            duration_seconds=session.duration_seconds,
            orientation=session.orientation,
            output_filename=output_path.name,
            file_size_bytes=file_size,
            thumbnail_filename=thumb_filename,
            tts_provider=session.tts_provider,
            tts_voice_key=session.tts_voice_key,
            tts_voice_label=session.tts_voice_label,
        )
        db.add(video)
        db.commit()
        db.refresh(video)
        return video.id
    except Exception as e:
        db.rollback()
        print(f"Warning: Failed to save video to database: {e}")
        return None
    finally:
        db.close()


def _first_render_outro(render_info: dict, loop_mode) -> float:
    """Freeze-frame outro seconds the first render added (0 in loop mode)."""
    if 'outro_duration' in (render_info or {}):
        return float(render_info.get('outro_duration') or 0)
    if loop_mode:
        return 0.0
    return float(os.getenv("VIDEO_OUTRO_DURATION", "1.0"))


def _first_render_fade(render_info: dict, loop_mode) -> float:
    """Fade-out seconds the first render used (0 in loop mode)."""
    if 'fade_duration' in (render_info or {}):
        return float(render_info.get('fade_duration') or 0)
    if loop_mode:
        return 0.0
    return float(os.getenv("FADE_OUT_DURATION", "1.0"))


def _first_render_subtitles(session, render_info: dict) -> dict:
    """Subtitle style + the values the first render actually drew captions with.

    ``overrides`` keeps only what the user explicitly chose, so the editor
    can tell a preset value from a customisation.
    """
    style = ((render_info or {}).get('subtitle_style') or session.subtitle_style
             or os.getenv("SUBTITLE_STYLE", "youtube"))
    overrides = (render_info or {}).get('subtitle_overrides')
    if overrides is None:
        overrides = _subtitle_overrides(session) or {}
    resolved = (render_info or {}).get('subtitle_settings')
    if not resolved:
        try:
            from subtitle_generator import SubtitleGenerator
            resolved = SubtitleGenerator(
                style=style, orientation=session.orientation,
                custom_overrides=overrides or None,
            ).resolved_settings()
        except Exception:
            resolved = {}
    return {
        "style": style,
        "font": resolved.get("font", "Arial"),
        "font_size": resolved.get("font_size", 52),
        "color": resolved.get("color", "#FFFFFF"),
        "outline_color": resolved.get("outline_color", "#000000"),
        "position": resolved.get("position", "bottom"),
        "overrides": dict(overrides or {}),
    }


def _persist_project_files(session, output_path, video_id):
    """Copy intermediate files to persistent projects/ directory and write project.json.

    Args:
        session: VideoSession with intermediate file paths
        output_path: Path to the final video
        video_id: Database video ID (used as project directory name)

    Returns:
        Path to the project directory, or None if persistence failed.
    """
    try:
        project_dir = Path(settings.projects_dir) / str(video_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        clips_dir = project_dir / "clips"
        clips_dir.mkdir(exist_ok=True)

        # Copy video_no_audio
        video_no_audio_dest = None
        if session.video_no_audio_path and Path(session.video_no_audio_path).exists():
            video_no_audio_dest = project_dir / "video_no_audio.mp4"
            shutil.copy2(session.video_no_audio_path, video_no_audio_dest)

        # Copy narration audio
        narration_dest = None
        if session.narration_audio_path and Path(session.narration_audio_path).exists():
            narration_dest = project_dir / "narration.wav"
            shutil.copy2(session.narration_audio_path, narration_dest)

        # Copy final audio (mixed with music) — only if different from narration
        audio_final_dest = None
        has_bg_music = (
            session.final_audio_path is not None
            and session.narration_audio_path is not None
            and session.final_audio_path != session.narration_audio_path
        )
        if has_bg_music and Path(session.final_audio_path).exists():
            audio_final_dest = project_dir / "audio_final.wav"
            shutil.copy2(session.final_audio_path, audio_final_dest)
        elif narration_dest:
            # No separate mixed file — use narration as the final audio
            audio_final_dest = narration_dest

        # Copy background music file for re-mixing in editor
        music_file_dest = None
        if session.music_file_path and Path(session.music_file_path).exists():
            music_ext = Path(session.music_file_path).suffix or ".mp3"
            music_file_dest = project_dir / f"background_music{music_ext}"
            shutil.copy2(session.music_file_path, music_file_dest)

        # Copy individual trimmed clips (each shot's untouched footage)
        clip_files = []
        copied = {}  # source path -> project-relative path
        if session.trimmed_clip_paths:
            for i, clip_path in enumerate(session.trimmed_clip_paths):
                src = Path(clip_path)
                if src.exists():
                    dest = clips_dir / f"clip_{i}.mp4"
                    shutil.copy2(src, dest)
                    clip_files.append(f"clips/clip_{i}.mp4")
                    copied[str(src)] = f"clips/clip_{i}.mp4"
                else:
                    clip_files.append(None)

        # The clip list the render actually concatenated: the loop copy of the
        # first clip and/or the last clip extended by the freeze-frame outro.
        render_info = session.render_info or {}
        render_clips = []
        for i, clip_path in enumerate(render_info.get('final_clips') or []):
            if str(clip_path) in copied:
                render_clips.append(copied[str(clip_path)])
                continue
            src = Path(clip_path)
            if not src.exists():
                render_clips = []
                break
            dest = clips_dir / f"render_{i}.mp4"
            shutil.copy2(src, dest)
            render_clips.append(f"clips/render_{i}.mp4")

        # Copy subtitles if they exist in temp dir
        subtitle_dest = None
        if session.temp_dir_path:
            for sub_name in ["subtitles_custom.ass", "subtitles.ass"]:
                sub_src = Path(session.temp_dir_path) / sub_name
                if sub_src.exists():
                    subtitle_dest = project_dir / "subtitles.ass"
                    shutil.copy2(sub_src, subtitle_dest)
                    break

        # Build project manifest
        project_data = {
            "version": 1,
            "video_id": video_id,
            "session_id": session.session_id,
            "title": session.title,
            "orientation": session.orientation,
            "duration_seconds": session.duration_seconds,
            "input_type": session.input_type,
            "prompt": session.prompt,
            "script_text": session.script_text,
            "narration_style": session.narration_style,
            "tts_provider": session.tts_provider,
            "tts_voice_key": session.tts_voice_key,
            "tts_voice_label": session.tts_voice_label,
            "loop_mode": bool(render_info.get('loop_mode', session.loop_mode)),
            "transition_duration": session.transition_duration or float(os.getenv("TRANSITION_DURATION", "0.5")),
            "transition_type": render_info.get('transition_type') or os.getenv("TRANSITION_TYPE", "crossfade"),
            "outro_duration": _first_render_outro(render_info, session.loop_mode),
            "fade_duration": _first_render_fade(render_info, session.loop_mode),
            "render_clips": render_clips or None,
            "word_timestamps": session.word_timestamps,
            "clips": [],
            "audio": {
                "narration_file": "narration.wav" if narration_dest else None,
                "mixed_file": "audio_final.wav" if has_bg_music and audio_final_dest else None,
                "music_file": music_file_dest.name if music_file_dest else None,
                "music_volume": float(os.getenv("MUSIC_VOLUME", "0.15")),
                "narration_volume": 1.0,
                "has_background_music": has_bg_music,
            },
            "subtitles": {
                "enabled": session.subtitles_enabled,
                **_first_render_subtitles(session, render_info),
                "file": "subtitles.ass" if subtitle_dest else None,
            },
            "output": {
                "video_no_audio": "video_no_audio.mp4" if video_no_audio_dest else None,
                "final_video": Path(output_path).name,
            },
        }

        # Add clip metadata
        for i, clip_dict in enumerate(session.clips or []):
            clip_entry = {
                "index": i,
                "keywords": clip_dict.get("keywords", []),
                "visual_description": clip_dict.get("visual_description", ""),
                "video_id": clip_dict.get("video_id"),
                "author": clip_dict.get("author"),
                "author_url": clip_dict.get("author_url"),
                "provider": clip_dict.get("provider") or "Pexels",
                "media_type": clip_dict.get("media_type") or "video",
                "duration_seconds": session.clip_duration or clip_dict.get("duration_seconds"),
                # source_file is never modified; edits write trimmed_file
                "file": clip_files[i] if i < len(clip_files) else None,
                "source_file": clip_files[i] if i < len(clip_files) else None,
                "trimmed_file": None,
                "trim_start": 0,
                "trim_end": 0,
                "pad_seconds": 0,
            }
            project_data["clips"].append(clip_entry)

        # Write project.json
        project_json_path = project_dir / "project.json"
        with open(project_json_path, "w", encoding="utf-8") as f:
            json.dump(project_data, f, indent=2, ensure_ascii=False)

        # Update DB with project path
        from app.db.base import SessionLocal
        from app.db.models import Video
        db = SessionLocal()
        try:
            video = db.query(Video).filter(Video.id == video_id).first()
            if video:
                video.project_data_path = str(project_dir)
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

        return project_dir

    except Exception as e:
        print(f"Warning: Failed to persist project files: {e}")
        return None


# Global video service instance
video_service = VideoService()
