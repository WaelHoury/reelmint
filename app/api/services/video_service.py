"""Video generation service for API."""

import os
from pathlib import Path
from typing import Dict, List, Optional
from rq import Queue
from redis import Redis

from app.core.config import settings, get_words_per_second
from app.api.services.session_store import VideoSession, session_store, ClipInfo


class VideoService:
    """Handles video generation business logic."""

    def __init__(self):
        """Initialize video service."""
        self.redis = Redis.from_url(settings.redis_url)
        self.queue = Queue('video_generation', connection=self.redis)

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
            wps = get_words_per_second()
            session.estimated_duration_seconds = session.word_count / wps
            session.status = "draft"
            session_store.update(session)
            return session

        # Generate script from prompt using LLM
        from app.core import ScenePlanner

        planner = ScenePlanner(
            provider=settings.llm_provider,
            model=settings.ollama_model if settings.llm_provider == "ollama" else settings.openrouter_model,
            base_url=settings.ollama_base_url if settings.llm_provider == "ollama" else settings.openrouter_base_url,
            api_key=settings.openrouter_api_key if settings.llm_provider == "openrouter" else None,
            narration_style=settings.narration_style,
        )

        duration_minutes = session.duration_seconds / 60
        wps = get_words_per_second()

        # Generate narration + clips via LLM, keep only the narration for now
        plan = planner.generate_continuous(
            prompt=session.prompt,
            duration_minutes=duration_minutes,
            words_per_second=wps,
            loop_mode=session.loop_mode,
        )

        session.script_text = plan.narration
        session.word_count = len(plan.narration.split())
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
        wps = get_words_per_second()
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
        """Generate visual clips for the script.

        Args:
            session: Session with confirmed script

        Returns:
            Updated session with clips
        """
        if session.status != "script_ready":
            raise ValueError("Script must be confirmed before generating clips")

        from app.core import ScenePlanner
        from app.media import VideoSearcher

        # Generate clip descriptions via LLM
        planner = ScenePlanner(
            provider=settings.llm_provider,
            model=settings.ollama_model if settings.llm_provider == "ollama" else settings.openrouter_model,
            base_url=settings.ollama_base_url if settings.llm_provider == "ollama" else settings.openrouter_base_url,
            api_key=settings.openrouter_api_key if settings.llm_provider == "openrouter" else None,
            narration_style=settings.narration_style,
        )

        duration_minutes = session.duration_seconds / 60
        num_clips = max(3, int(session.duration_seconds / 3))

        # Generate clips from script using LLM
        plan = planner.generate_clips_from_script(session.script_text, duration_minutes)

        # Search Pexels for each clip
        searcher = VideoSearcher(api_key=settings.pexels_api_key)
        session_clips = []

        for i, clip in enumerate(plan.clips):
            # Search for videos matching keywords
            videos = searcher.search(clip.keywords, max_results=5, orientation=session.orientation)

            video_info = None
            if videos:
                video_info = videos[0]  # Take first match for now

            clip_info = {
                'index': i,
                'keywords': clip.keywords,
                'visual_description': clip.visual_description,
                'video_preview_url': video_info.download_url if video_info else None,
                'video_id': str(video_info.id) if video_info else None,
                'author': video_info.author if video_info else None,
                'author_url': video_info.url if video_info else None,
                'duration_seconds': video_info.duration if video_info else None,
            }
            session_clips.append(clip_info)

        session.clips = session_clips
        session.status = "clips_ready"
        session_store.update(session)
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

        from app.media import VideoSearcher

        # Extract keywords from description if not provided
        if keywords is None:
            # Simple keyword extraction - take nouns from description
            words = visual_description.lower().split()
            # Filter common words, keep potential nouns
            stopwords = {'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'and', 'or', 'is', 'are', 'was', 'were'}
            keywords = [w for w in words if w not in stopwords and len(w) > 2][:5]

        # Search for new video
        searcher = VideoSearcher(api_key=settings.pexels_api_key)
        videos = searcher.search(keywords, max_results=5, orientation=session.orientation)

        video_info = videos[0] if videos else None

        session.clips[clip_index] = {
            'index': clip_index,
            'keywords': keywords,
            'visual_description': visual_description,
            'video_preview_url': video_info.download_url if video_info else None,
            'video_id': str(video_info.id) if video_info else None,
            'author': video_info.author if video_info else None,
            'author_url': video_info.url if video_info else None,
            'duration_seconds': video_info.duration if video_info else None,
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

    def queue_video_generation(self, session: VideoSession) -> str:
        """Queue video generation as background job.

        Args:
            session: Session ready for generation

        Returns:
            Job ID
        """
        if session.status != "clips_ready":
            raise ValueError("Clips must be confirmed before generating video")

        session.status = "generating"
        session_store.update(session)

        # Queue the job
        job = self.queue.enqueue(
            'app.api.services.video_service.generate_video_task',
            session.session_id,
            job_timeout='30m',
        )

        session.job_id = job.id
        session_store.update(session)
        return job.id

    def get_job_status(self, session: VideoSession) -> Dict:
        """Get video generation job status.

        Args:
            session: Session with job

        Returns:
            Job status dict
        """
        if not session.job_id:
            return {
                'status': session.status,
                'job_id': None,
            }

        from rq.job import Job
        try:
            job = Job.fetch(session.job_id, connection=self.redis)
            status = job.get_status()

            return {
                'job_id': session.job_id,
                'status': status,
                'progress_percent': session.progress_percent,
                'current_step': session.current_step,
                'error_message': session.error_message if status == 'failed' else None,
                'output_url': f"/api/sessions/{session.session_id}/video" if status == 'finished' else None,
            }
        except Exception:
            return {
                'job_id': session.job_id,
                'status': session.status,
                'error_message': session.error_message,
            }


def generate_video_task(session_id: str):
    """Background task for video generation.

    Args:
        session_id: Session ID to generate video for
    """
    session = session_store.get(session_id)
    if not session:
        raise ValueError(f"Session not found: {session_id}")

    try:
        from generate_video import generate_video
        from pathlib import Path

        # Update progress
        session.current_step = "Starting video generation..."
        session.progress_percent = 5
        session_store.update(session)

        # Generate video using existing function
        output_path = Path(settings.output_dir) / f"{session.title}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        generate_video(
            prompt=session.prompt,
            duration_minutes=session.duration_seconds / 60,
            output_path=output_path,
            orientation=session.orientation,
            script_text=session.script_text,
            keep_temp=False,
        )

        session.status = "completed"
        session.output_path = str(output_path)
        session.progress_percent = 100
        session.current_step = "Complete!"
        session_store.update(session)

    except Exception as e:
        session.status = "failed"
        session.error_message = str(e)
        session_store.update(session)
        raise


# Global video service instance
video_service = VideoService()
