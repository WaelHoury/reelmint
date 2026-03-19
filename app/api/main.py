"""FastAPI application for AI Video Maker API."""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.api.limiter import limiter
from app.api.routes import (
    sessions_router,
    scripts_router,
    clips_router,
    videos_router,
    config_router,
    auth_router,
    library_router,
    editor_router,
    tts_router,
    publish_router,
    video_publish_router,
)

logger = logging.getLogger(__name__)


async def _stale_job_cleanup_loop():
    """Periodically detect and reset stale generating/clips_generating sessions."""
    from redis import Redis
    from datetime import datetime, timezone
    from app.api.services.session_store import session_store
    from app.api.services.video_service import VideoService, _release_generation_lock

    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            redis_conn = Redis.from_url(settings.redis_url)
            sessions = session_store.list_all(limit=200)

            for session in sessions:
                # --- Stale video generation ---
                if session.status == "generating":
                    heartbeat = redis_conn.get(f"job_heartbeat:{session.session_id}")
                    if heartbeat:
                        continue  # Worker is alive

                    # Check if RQ job still exists
                    job_alive = False
                    if session.job_id:
                        try:
                            from rq.job import Job
                            job = Job.fetch(session.job_id, connection=redis_conn)
                            rq_status = job.get_status()
                            if rq_status in ("queued", "started", "deferred", "scheduled"):
                                job_alive = True
                        except Exception:
                            pass

                    if not job_alive:
                        logger.warning("Resetting stale generating session %s (no heartbeat, no active job)", session.session_id)
                        session.status = "failed"
                        session.error_message = "Generation was interrupted. Please try again."
                        session_store.update(session)
                        _release_generation_lock(session)

                # --- Stale clip generation ---
                elif session.status == "clips_generating":
                    # Clip gen is a daemon thread — check if stuck for >15 min
                    try:
                        created = datetime.fromisoformat(session.created_at)
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        age_minutes = (now - created).total_seconds() / 60
                        if age_minutes > 15:
                            logger.warning("Resetting stale clips_generating session %s (>15 min)", session.session_id)
                            session.status = "script_ready"
                            session.error_message = "Clip generation was interrupted. Please try again."
                            session_store.update(session)
                    except Exception:
                        pass

        except Exception:
            logger.exception("Stale job cleanup error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run database migrations and temp cleanup on startup."""
    from alembic.config import Config
    from alembic import command
    import os

    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "db", "migrations"))
    try:
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied successfully.")
    except Exception as e:
        logger.warning("Could not run migrations: %s", e)

    # Clean up old temp directories in background
    from app.utils.helpers import cleanup_old_temp_dirs
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, cleanup_old_temp_dirs, None, settings.temp_retention_hours)

    # Start stale job cleanup loop
    cleanup_task = asyncio.create_task(_stale_job_cleanup_loop())

    yield

    # Cancel cleanup on shutdown
    cleanup_task.cancel()

# Create FastAPI app
app = FastAPI(
    title="AI Video Maker API",
    description="REST API for generating YouTube videos from text prompts or scripts",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Return 429 with a clear message when rate limit is exceeded."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please wait before trying again."},
    )

# CORS middleware — origins configured via CORS_ORIGINS env var
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# Health check endpoint
@app.get("/health", tags=["health"])
async def health_check():
    """Check API health."""
    return {"status": "healthy"}


# Include routers
app.include_router(sessions_router, prefix="/api")
app.include_router(scripts_router, prefix="/api")
app.include_router(clips_router, prefix="/api")
app.include_router(videos_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(library_router, prefix="/api")
app.include_router(editor_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(publish_router, prefix="/api")
app.include_router(video_publish_router, prefix="/api")


# Root endpoint
@app.get("/", tags=["root"])
async def root():
    """API root - returns basic info."""
    return {
        "name": "AI Video Maker API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
