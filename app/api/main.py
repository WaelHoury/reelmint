"""FastAPI application for AI Video Maker API."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    sessions_router,
    scripts_router,
    clips_router,
    videos_router,
    config_router,
    auth_router,
    library_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run database migrations on startup."""
    from alembic.config import Config
    from alembic import command
    import os

    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "db", "migrations"))
    try:
        command.upgrade(alembic_cfg, "head")
        print("Database migrations applied successfully.")
    except Exception as e:
        print(f"Warning: Could not run migrations: {e}")
    yield

# Create FastAPI app
app = FastAPI(
    title="AI Video Maker API",
    description="REST API for generating YouTube videos from text prompts or scripts",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
