"""Video generation service for API."""

import json
import logging
import os
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
        from app.core import ScenePlanner

        style = session.narration_style or settings.narration_style
        planner = ScenePlanner(
            provider=settings.llm_provider,
            model=settings.ollama_model if settings.llm_provider == "ollama" else settings.openrouter_model,
            base_url=settings.ollama_base_url if settings.llm_provider == "ollama" else settings.openrouter_base_url,
            api_key=settings.openrouter_api_key if settings.llm_provider == "openrouter" else None,
            narration_style=style,
        )

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
        if settings.enable_pixabay and settings.pixabay_api_key:
            from app.media.pixabay_search import CachedPixabaySearcher
            pixabay_searcher = CachedPixabaySearcher(
                api_key=settings.pixabay_api_key,
                redis_client=redis_client,
                cache_ttl=settings.clip_search_cache_ttl,
            )

        searcher = MultiSourceSearcher(
            pexels_searcher=pexels_searcher,
            pixabay_searcher=pixabay_searcher,
            enable_pixabay=settings.enable_pixabay,
            enable_image_fallback=settings.enable_image_fallback,
        )
        videos = searcher.search(keywords, max_results=5, orientation=session.orientation)

        video_info = videos[0] if videos else None

        session.clips[clip_index] = {
            'index': clip_index,
            'keywords': keywords,
            'visual_description': visual_description,
            'video_preview_url': video_info.preview_url if video_info else None,
            'video_id': str(video_info.id) if video_info else None,
            'author': video_info.author if video_info else None,
            'author_url': video_info.url if video_info else None,
            'duration_seconds': video_info.duration if video_info else None,
            'search_status': 'ready' if video_info else 'failed',
            'media_type': 'video' if video_info else None,
            'provider': video_info.provider if video_info else None,
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
        if session.status not in ("clips_ready",):
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

        session.status = "generating"
        session_store.update(session)

        # Queue the job
        job = self.queue.enqueue(
            'app.api.services.video_service.generate_video_task',
            session.session_id,
            job_timeout='45m',
        )

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
            # No job enqueued yet — only return "processing"/"queued" if session
            # is actually in a generating state; otherwise return the raw status
            # so the frontend knows it still needs to POST /generate.
            if session.status == "generating":
                return {'status': 'processing', 'job_id': None}
            return {
                'status': session.status,
                'job_id': None,
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

        from app.core import ScenePlanner
        from video_searcher import CachedVideoSearcher
        from app.media.multi_source_searcher import MultiSourceSearcher

        # --- Stage 1: LLM clip descriptions ---
        t_llm = time.time()
        planner = ScenePlanner(
            provider=settings.llm_provider,
            model=settings.ollama_model if settings.llm_provider == "ollama" else settings.openrouter_model,
            base_url=settings.ollama_base_url if settings.llm_provider == "ollama" else settings.openrouter_base_url,
            api_key=settings.openrouter_api_key if settings.llm_provider == "openrouter" else None,
            narration_style=settings.narration_style,
        )
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
        if settings.enable_pixabay and settings.pixabay_api_key:
            from app.media.pixabay_search import CachedPixabaySearcher
            pixabay_searcher = CachedPixabaySearcher(
                api_key=settings.pixabay_api_key,
                redis_client=redis_client,
                cache_ttl=settings.clip_search_cache_ttl,
            )

        searcher = MultiSourceSearcher(
            pexels_searcher=pexels_searcher,
            pixabay_searcher=pixabay_searcher,
            enable_pixabay=settings.enable_pixabay,
            enable_image_fallback=settings.enable_image_fallback,
        )

        total_clips = len(plan.clips)
        completed = 0

        def _search_one(idx, clip):
            """Search for a single clip. Returns (index, clip_dict)."""
            try:
                videos = searcher.search(clip.keywords, max_results=5, orientation=session.orientation)
                video_info = videos[0] if videos else None

                if not video_info:
                    # Retry with fewer keywords
                    if len(clip.keywords) > 2:
                        videos = searcher.search(clip.keywords[:2], max_results=5, orientation=session.orientation)
                        video_info = videos[0] if videos else None

                if not video_info:
                    # Final generic fallback
                    generic = [clip.keywords[0]] if clip.keywords else ['nature']
                    videos = searcher.search(generic, max_results=5, orientation=session.orientation)
                    video_info = videos[0] if videos else None

                # Image fallback via Pixabay
                if not video_info and settings.enable_image_fallback:
                    images = searcher.search_images(clip.keywords, max_results=3, orientation=session.orientation)
                    if images:
                        image_info = images[0]
                        return idx, {
                            'index': idx,
                            'keywords': clip.keywords,
                            'visual_description': clip.visual_description,
                            'video_preview_url': image_info.preview_url,
                            'video_id': str(image_info.id),
                            'author': image_info.author,
                            'author_url': image_info.url,
                            'duration_seconds': None,
                            'search_status': 'ready',
                            'search_error': None,
                            'media_type': 'image',
                            'provider': 'Pixabay',
                        }

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
                    'provider': video_info.provider if video_info else None,
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

        stats = searcher.cache_stats
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
        )

        session.status = "completed"
        session.output_path = str(output_path)
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


def regenerate_subtitles(session: VideoSession, subtitle_settings: dict) -> VideoSession:
    """Re-generate subtitles with custom settings without re-running the full pipeline.

    Args:
        session: Completed session with intermediate files
        subtitle_settings: Dict with keys: style, font, font_size, color, outline_color, position

    Returns:
        Updated session
    """
    if session.status != "completed":
        raise ValueError("Video must be completed before editing subtitles")

    if not session.video_no_audio_path or not session.final_audio_path:
        raise ValueError("Intermediate files not available for subtitle re-generation")

    from pathlib import Path
    from subtitle_generator import SubtitleGenerator
    from effects_engine import EffectsEngine
    import os

    video_no_audio = Path(session.video_no_audio_path)
    final_audio = Path(session.final_audio_path)
    temp_dir = Path(session.temp_dir_path) if session.temp_dir_path else video_no_audio.parent

    # Fallback to project directory if temp files are gone
    if not video_no_audio.exists() or not final_audio.exists():
        project_dir = _find_project_dir(session)
        if project_dir:
            project_json = project_dir / "project.json"
            if project_json.exists():
                with open(project_json, "r") as f:
                    project = json.load(f)
                vna = project.get("output", {}).get("video_no_audio")
                af = project.get("audio", {}).get("mixed_file") or project.get("audio", {}).get("narration_file")
                if vna:
                    video_no_audio = project_dir / vna
                if af:
                    final_audio = project_dir / af
                temp_dir = project_dir

    # Generate video_no_audio on-demand if it doesn't exist
    if not video_no_audio.exists() and project_dir:
        from app.api.services.editor_service import EditorService
        editor_svc = EditorService()
        proj_json = project_dir / "project.json"
        if proj_json.exists():
            with open(proj_json, "r") as f:
                proj = json.load(f)
            try:
                video_no_audio = editor_svc._ensure_video_no_audio(project_dir, proj)
            except ValueError:
                pass

    if not video_no_audio.exists() or not final_audio.exists():
        raise ValueError("Intermediate video/audio files no longer exist on disk")

    # Build custom overrides from settings
    custom_overrides = {}
    if subtitle_settings.get('font'):
        custom_overrides['font'] = subtitle_settings['font']
    if subtitle_settings.get('font_size'):
        custom_overrides['font_size'] = subtitle_settings['font_size']
    if subtitle_settings.get('color'):
        custom_overrides['color'] = subtitle_settings['color']
    if subtitle_settings.get('outline_color'):
        custom_overrides['outline_color'] = subtitle_settings['outline_color']
    if subtitle_settings.get('position'):
        custom_overrides['position'] = subtitle_settings['position']

    style_preset = subtitle_settings.get('style', os.getenv("SUBTITLE_STYLE", "youtube"))

    # Generate new subtitle file
    sub_gen = SubtitleGenerator(
        style=style_preset,
        orientation=session.orientation,
        custom_overrides=custom_overrides if custom_overrides else None,
    )

    subtitle_file = temp_dir / "subtitles_custom.ass"
    word_timestamps = session.word_timestamps

    # Generate heuristic timestamps if empty (e.g. Fish TTS where ASR failed)
    if not word_timestamps and session.script_text and session.narration_audio_path:
        narration_path = Path(session.narration_audio_path)
        if narration_path.exists():
            from video_assembler import VideoAssembler
            assembler = VideoAssembler(
                ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
                ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
            )
            try:
                total_duration = assembler.get_duration(narration_path)
                from tts_generator import TTSGenerator
                words = session.script_text.split()
                total_weight = sum(TTSGenerator._get_word_weight(w) for w in words)
                if total_weight > 0:
                    subtitle_offset = 0.15
                    t = subtitle_offset
                    word_timestamps = []
                    for word in words:
                        weight = TTSGenerator._get_word_weight(word)
                        word_dur = total_duration * (weight / total_weight)
                        word_timestamps.append({
                            'word': word,
                            'start': round(t, 3),
                            'end': round(t + word_dur, 3),
                        })
                        t += word_dur
                    session.word_timestamps = word_timestamps
                    session_store.update(session)
            except Exception:
                pass

    if word_timestamps:
        if session.orientation in ('portrait', 'square'):
            sub_gen.generate_karaoke_ass(word_timestamps, subtitle_file)
        else:
            sub_gen.generate_phrase_ass(word_timestamps, subtitle_file)
    else:
        # Fallback to sentence-based
        import re
        narration = session.script_text or ""
        sentences = re.split(r'(?<=[.!?])\s+', narration.strip())
        sentences = [s for s in sentences if s.strip()]
        if sentences:
            total_duration = session.estimated_duration_seconds or 60
            total_chars = sum(len(s) for s in sentences)
            subtitle_scenes = []
            current_time = 0.0
            for sentence in sentences:
                dur = total_duration * (len(sentence) / total_chars) if total_chars > 0 else 0
                subtitle_scenes.append({
                    'narration': sentence,
                    'start_time': current_time,
                    'duration': dur,
                })
                current_time += dur
            sub_gen.generate_ass(subtitle_scenes, subtitle_file)

    # Re-run finalize with new subtitles
    finalizer = EffectsEngine(
        ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
        ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
    )

    output_path = Path(session.output_path)

    # Check for subscribe overlay
    subscribe_video_path = None
    subscribe_params = None
    if os.getenv("ENABLE_SUBSCRIBE_OVERLAY", "false").lower() == "true":
        subscribe_path = Path(os.getenv("SUBSCRIBE_VIDEO_PATH", "subscribe.mp4"))
        if not subscribe_path.is_absolute():
            subscribe_path = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent.parent / subscribe_path
        if subscribe_path.exists():
            subscribe_video_path = subscribe_path
            subscribe_params = {
                'orientation': session.orientation,
                'scale': float(os.getenv("SUBSCRIBE_SCALE", "0")) or None,
                'margin_bottom': int(os.getenv("SUBSCRIBE_MARGIN_BOTTOM", "50")),
                'chromakey_color': os.getenv("SUBSCRIBE_CHROMAKEY_COLOR", "0x00FF00"),
                'chromakey_similarity': float(os.getenv("SUBSCRIBE_CHROMAKEY_SIMILARITY", "0.3")),
                'chromakey_blend': float(os.getenv("SUBSCRIBE_CHROMAKEY_BLEND", "0.15")),
            }

    fade_dur = 0
    if not session.loop_mode:
        fade_dur = float(os.getenv("FADE_OUT_DURATION", "1.0"))

    finalizer.finalize_video(
        video_no_audio=video_no_audio,
        audio_path=final_audio,
        output_path=output_path,
        subtitle_path=subtitle_file,
        subscribe_video=subscribe_video_path,
        subscribe_params=subscribe_params,
        fade_duration=fade_dur,
    )

    # Save subtitle settings to session
    session.subtitle_style = subtitle_settings.get('style')
    session.subtitle_font = subtitle_settings.get('font')
    session.subtitle_font_size = subtitle_settings.get('font_size')
    session.subtitle_color = subtitle_settings.get('color')
    session.subtitle_outline_color = subtitle_settings.get('outline_color')
    session.subtitle_position = subtitle_settings.get('position')
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

        # Copy individual trimmed clips
        clip_files = []
        if session.trimmed_clip_paths:
            for i, clip_path in enumerate(session.trimmed_clip_paths):
                src = Path(clip_path)
                if src.exists():
                    dest = clips_dir / f"clip_{i}.mp4"
                    shutil.copy2(src, dest)
                    clip_files.append(f"clips/clip_{i}.mp4")
                else:
                    clip_files.append(None)

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
            "loop_mode": session.loop_mode,
            "transition_duration": session.transition_duration or float(os.getenv("TRANSITION_DURATION", "0.5")),
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
                "style": session.subtitle_style or os.getenv("SUBTITLE_STYLE", "youtube"),
                "font": session.subtitle_font or "Arial",
                "font_size": session.subtitle_font_size or 52,
                "color": session.subtitle_color or "#FFFFFF",
                "outline_color": session.subtitle_outline_color or "#000000",
                "position": session.subtitle_position or "bottom",
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
                "duration_seconds": session.clip_duration or clip_dict.get("duration_seconds"),
                "file": clip_files[i] if i < len(clip_files) else None,
                "trim_start": 0,
                "trim_end": 0,
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
