"""SQLAlchemy engine, session, and base configuration."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

_engine_kwargs = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    # SQLite (tests/local dev) uses SingletonThreadPool/StaticPool, which
    # reject QueuePool sizing args; allow use across threads instead.
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(
        pool_size=20,
        max_overflow=30,
        pool_timeout=30,
        pool_recycle=1800,
    )

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency that provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
