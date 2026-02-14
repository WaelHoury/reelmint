"""Tests for duplicate title validation on session creation."""

import os
import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Provide lightweight google auth stubs for environments without google-auth.
if "google.oauth2" not in sys.modules:
    google_module = types.ModuleType("google")
    oauth2_module = types.ModuleType("google.oauth2")
    id_token_module = types.ModuleType("google.oauth2.id_token")
    id_token_module.verify_oauth2_token = lambda *args, **kwargs: {}
    oauth2_module.id_token = id_token_module

    auth_module = types.ModuleType("google.auth")
    transport_module = types.ModuleType("google.auth.transport")
    requests_module = types.ModuleType("google.auth.transport.requests")
    requests_module.Request = object
    transport_module.requests = requests_module
    auth_module.transport = transport_module

    google_module.oauth2 = oauth2_module
    google_module.auth = auth_module

    sys.modules["google"] = google_module
    sys.modules["google.oauth2"] = oauth2_module
    sys.modules["google.oauth2.id_token"] = id_token_module
    sys.modules["google.auth"] = auth_module
    sys.modules["google.auth.transport"] = transport_module
    sys.modules["google.auth.transport.requests"] = requests_module

# Provide lightweight jose stubs for environments without python-jose.
if "jose" not in sys.modules:
    jose_module = types.ModuleType("jose")
    jose_module.JWTError = Exception
    jwt_module = types.ModuleType("jose.jwt")
    jwt_module.encode = lambda *args, **kwargs: "token"
    jwt_module.decode = lambda *args, **kwargs: {}
    jose_module.jwt = jwt_module
    sys.modules["jose"] = jose_module
    sys.modules["jose.jwt"] = jwt_module

# Use in-memory SQLite in tests to avoid requiring Postgres drivers at import time.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.api.routes.sessions import router as sessions_router
from app.api.routes.sessions import get_current_user, get_db


class _FakeQuery:
    """Minimal query stub for videos lookup in create_session."""

    def __init__(self, videos):
        self._videos = videos

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._videos


class _FakeDB:
    """Minimal DB session stub for dependency override."""

    def __init__(self, videos):
        self._videos = videos

    def query(self, _model):
        return _FakeQuery(self._videos)


class SessionDuplicateTitleTests(TestCase):
    """Session API duplicate-title checks."""

    @staticmethod
    def _payload(title="My Video"):
        return {
            "title": title,
            "input_type": "prompt",
            "prompt": "A short topic",
            "duration_seconds": 30,
            "loop_mode": False,
            "subtitles_enabled": True,
            "orientation": "landscape",
        }

    @staticmethod
    def _fake_session(title):
        return SimpleNamespace(
            session_id="session-1",
            status="draft",
            title=title,
            created_at=datetime.utcnow(),
            input_type="prompt",
            duration_seconds=30,
            loop_mode=False,
            subtitles_enabled=True,
            orientation="landscape",
            script_text=None,
            word_count=None,
            estimated_duration_seconds=None,
            error_message=None,
        )

    @staticmethod
    def _build_test_client(user_id, videos):
        app = FastAPI()
        app.include_router(sessions_router, prefix="/api")

        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        def _override_db():
            yield _FakeDB(videos)

        app.dependency_overrides[get_db] = _override_db
        return TestClient(app)

    def test_create_session_new_title_returns_201_and_normalizes_title(self):
        client = self._build_test_client(user_id=1, videos=[SimpleNamespace(title="Existing Video")])
        captured = {}

        def _create_stub(**kwargs):
            captured.update(kwargs)
            return self._fake_session(kwargs["title"])

        with patch("app.api.routes.sessions.session_store.create", side_effect=_create_stub):
            response = client.post("/api/sessions", json=self._payload("  New   Video  "))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(captured["title"], "New Video")
        self.assertEqual(response.json()["title"], "New Video")

    def test_duplicate_title_returns_409_with_structured_detail(self):
        client = self._build_test_client(user_id=1, videos=[SimpleNamespace(title="My    Video")])

        with patch("app.api.routes.sessions.session_store.create") as create_mock:
            response = client.post("/api/sessions", json=self._payload(" my video "))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "DUPLICATE_TITLE",
                "field": "title",
                "message": "A video with this title already exists. Choose a different title.",
            },
        )
        create_mock.assert_not_called()

    def test_same_title_for_another_user_is_allowed(self):
        client = self._build_test_client(user_id=2, videos=[])

        with patch(
            "app.api.routes.sessions.session_store.create",
            side_effect=lambda **kwargs: self._fake_session(kwargs["title"]),
        ):
            response = client.post("/api/sessions", json=self._payload("Shared Title"))

        self.assertEqual(response.status_code, 201)

    def test_non_duplicate_validation_error_still_returns_400(self):
        client = self._build_test_client(user_id=1, videos=[])

        with patch(
            "app.api.routes.sessions.session_store.create",
            side_effect=ValueError("duration_seconds is required when input_type is 'prompt'"),
        ):
            bad_payload = self._payload("Unique Title")
            bad_payload["duration_seconds"] = None
            response = client.post("/api/sessions", json=bad_payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "duration_seconds is required when input_type is 'prompt'",
        )
