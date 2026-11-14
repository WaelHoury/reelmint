"""Shared test setup.

Runs before any test module is imported, so app settings never pick up a real
database, API keys or storage from a developer's local .env file.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# load_dotenv() never overrides variables that are already set, so these win.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["USE_R2_STORAGE"] = "false"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ.setdefault("PEXELS_API_KEY", "test-key")


import pytest  # noqa: E402


@pytest.fixture
def batch_env(monkeypatch):
    """Isolated SQLite DB + fakeredis wired into the batch service and session store."""
    fakeredis = pytest.importorskip("fakeredis")
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base
    from app.db import models
    from app.api.services import batch_service
    from app.api.services.session_store import session_store

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    server = fakeredis.FakeServer()
    redis_bytes = fakeredis.FakeRedis(server=server)
    monkeypatch.setattr(batch_service, "SessionLocal", TestSession)
    monkeypatch.setattr(batch_service, "_redis_conn", redis_bytes)
    monkeypatch.setattr(session_store, "_redis", fakeredis.FakeRedis(server=server, decode_responses=True))

    db = TestSession()
    user = models.User(google_id="local", email="local@localhost", name="Local User")
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()

    yield SimpleNamespace(engine=engine, Session=TestSession, redis=redis_bytes, user_id=user_id)
    engine.dispose()
