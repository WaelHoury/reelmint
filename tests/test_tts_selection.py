"""Tests for TTS selection validation, preview auth, and Fish provider wiring."""

import os
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi import HTTPException

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
from app.api.routes import tts as tts_routes
from app.api.routes.tts import router as tts_router
from app.api.deps import get_current_user_from_header_or_token
from app.api.services.tts_service import (
    resolve_default_tts_selection_for_user,
    resolve_tts_selection_for_user,
)
from app.tts.base import VoiceCatalogEntry
from app.tts.fish_provider import FishAudioProvider
from app.tts.registry import get_configured_provider_names


class _FakeQuery:
    def __init__(self, videos):
        self._videos = videos

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._videos


class _FakeDB:
    def __init__(self, videos):
        self._videos = videos

    def query(self, _model):
        return _FakeQuery(self._videos)


class _FakeFishProvider:
    def list_voices(self, user_id=None):
        return []


class _FakeRegistry:
    enabled_providers = ["fish"]

    def get_provider(self, name):
        assert name == "fish"
        return _FakeFishProvider()


class _FakeSessionLocal:
    def __init__(self, voice):
        self._voice = voice

    def query(self, _model):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._voice

    def close(self):
        return None


class TTSSelectionTests(TestCase):
    @staticmethod
    def _payload():
        return {
            "title": "My Video",
            "input_type": "prompt",
            "prompt": "A short topic",
            "duration_seconds": 30,
            "loop_mode": False,
            "subtitles_enabled": True,
            "orientation": "landscape",
        }

    @staticmethod
    def _build_session_client(user_id=1, videos=None):
        app = FastAPI()
        app.include_router(sessions_router, prefix="/api")

        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        def _override_db():
            yield _FakeDB(videos or [])

        app.dependency_overrides[get_db] = _override_db
        return TestClient(app)

    def test_create_session_rejects_mismatched_provider_and_voice_key(self):
        client = self._build_session_client()
        payload = self._payload()
        payload["tts_selection"] = {
            "provider": "kokoro",
            "voice_key": "fish:voice-123",
            "label": "Private Fish Voice",
        }

        with patch("app.api.routes.sessions.session_store.create") as create_mock:
            response = client.post("/api/sessions", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "provider must match the provider encoded in voice_key",
        )
        create_mock.assert_not_called()

    def test_resolve_tts_selection_rejects_unknown_fish_voice(self):
        with patch("app.api.services.tts_service.get_tts_registry", return_value=_FakeRegistry()):
            with patch("app.api.services.tts_service.SessionLocal", return_value=_FakeSessionLocal(None)):
                with self.assertRaises(HTTPException) as ctx:
                    resolve_tts_selection_for_user(
                        provider="fish",
                        voice_key="fish:missing-voice",
                        user_id=1,
                    )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "Voice not found")

    def test_default_tts_selection_prefers_available_default_provider(self):
        catalog = [
            VoiceCatalogEntry(
                voice_key="kokoro:af_heart",
                provider="kokoro",
                voice_id="af_heart",
                label="Kokoro",
            ),
            VoiceCatalogEntry(
                voice_key="fish:voice-1",
                provider="fish",
                voice_id="voice-1",
                label="Fish",
            ),
        ]

        with patch("app.api.services.tts_service.settings.tts_default_voice_key", "fish:missing"):
            with patch("app.api.services.tts_service.settings.tts_default_provider", "fish"):
                resolved = resolve_default_tts_selection_for_user(user_id=1, catalog=catalog)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.voice_key, "fish:voice-1")

    def test_configured_providers_do_not_force_legacy_engine_into_catalog(self):
        with patch.dict(os.environ, {"TTS_ENABLED_PROVIDERS": "fish"}, clear=False):
            with patch("app.tts.registry.settings.tts_engine", "kokoro"):
                providers = get_configured_provider_names()

        self.assertEqual(providers, ["fish"])

    def test_preview_route_accepts_token_query_param(self):
        app = FastAPI()
        app.include_router(tts_router, prefix="/api")
        app.dependency_overrides[get_current_user_from_header_or_token] = lambda: SimpleNamespace(id=1)

        client = TestClient(app)
        with tempfile.TemporaryDirectory() as temp_dir:
            preview_dir = Path(temp_dir) / "tts_previews"
            preview_dir.mkdir(parents=True, exist_ok=True)
            cached_file = preview_dir / "kokoro_af_heart.wav"
            cached_file.write_bytes(b"fake-wav")

            with patch("app.api.routes.tts.settings.cache_dir", Path(temp_dir)):
                with patch(
                    "app.api.routes.tts.resolve_tts_selection_for_user",
                    return_value=VoiceCatalogEntry(
                        voice_key="kokoro:af_heart",
                        provider="kokoro",
                        voice_id="af_heart",
                        label="Kokoro Voice",
                    ),
                ):
                    response = client.get(
                        "/api/tts/preview",
                        params={"voice_key": "kokoro:af_heart", "token": "test-token"},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.content, b"fake-wav")

    def test_preview_route_returns_404_when_preview_sample_is_unavailable(self):
        app = FastAPI()
        app.include_router(tts_router, prefix="/api")
        app.dependency_overrides[get_current_user_from_header_or_token] = lambda: SimpleNamespace(id=1)

        class _MissingPreviewRegistry:
            @staticmethod
            def generate_preview(*args, **kwargs):
                raise FileNotFoundError("Preview unavailable")

        client = TestClient(app)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.api.routes.tts.settings.cache_dir", Path(temp_dir)):
                with patch(
                    "app.api.routes.tts.resolve_tts_selection_for_user",
                    return_value=VoiceCatalogEntry(
                        voice_key="fish:voice-1",
                        provider="fish",
                        voice_id="voice-1",
                        label="Fish Voice",
                    ),
                ):
                    with patch("app.tts.get_tts_registry", return_value=_MissingPreviewRegistry()):
                        response = client.get(
                            "/api/tts/preview",
                            params={"voice_key": "fish:voice-1", "token": "test-token"},
                        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Preview unavailable for this voice")

    def test_preview_route_downloads_private_clone_preview_from_r2(self):
        app = FastAPI()
        app.include_router(tts_router, prefix="/api")
        app.dependency_overrides[get_current_user_from_header_or_token] = lambda: SimpleNamespace(id=1)

        class _FakeR2:
            enabled = True

            @staticmethod
            def file_exists(_key):
                return True

            @staticmethod
            def download_file(_key, local_path):
                local_path.write_bytes(b"r2-preview")
                return local_path

        client = TestClient(app)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.api.routes.tts.settings.cache_dir", Path(temp_dir)):
                with patch("app.api.routes.tts.settings.use_r2_storage", True):
                    with patch(
                        "app.api.routes.tts.resolve_tts_selection_for_user",
                        return_value=VoiceCatalogEntry(
                            voice_key="fish:voice-1",
                            provider="fish",
                            voice_id="voice-1",
                            label="My Clone",
                            is_cloned=True,
                            owner_user_id=1,
                            tags=["cloned", "private"],
                        ),
                    ):
                        with patch("app.api.services.r2_storage.r2_storage", _FakeR2()):
                            with patch("app.tts.get_tts_registry") as registry_mock:
                                response = client.get(
                                    "/api/tts/preview",
                                    params={"voice_key": "fish:voice-1", "token": "test-token"},
                                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"r2-preview")
        registry_mock.assert_not_called()

    def test_fish_create_clone_forwards_private_clone_options(self):
        captured = {}

        class _FakeVoices:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(id="fish-123", state="training")

        provider = FishAudioProvider()
        provider._client = SimpleNamespace(voices=_FakeVoices())

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(b"voice")
            sample_path = Path(tmp.name)

        try:
            result = provider.create_clone(
                title="My Voice",
                audio_path=sample_path,
                user_id=42,
                transcript="hello world",
            )
        finally:
            sample_path.unlink(missing_ok=True)

        self.assertEqual(result["fish_voice_id"], "fish-123")
        self.assertEqual(result["status"], "processing")
        self.assertEqual(captured["visibility"], "private")
        self.assertEqual(captured["type"], "tts")
        self.assertTrue(captured["enhance_audio_quality"])
        self.assertEqual(captured["texts"], ["hello world"])

    def test_fish_list_voices_includes_curated_defaults_when_public_disabled(self):
        provider = FishAudioProvider()

        with patch.object(provider, "_get_user_cloned_voices", return_value=[]):
            with patch("app.tts.fish_provider.settings.fish_preset_voice_ids", ""):
                with patch("app.tts.fish_provider.settings.fish_public_voices_enabled", False):
                    voices = provider.list_voices(user_id=1)

        self.assertEqual(
            [voice.voice_key for voice in voices],
            [
                "fish:b347db033a6549378b48d00acb0d06cd",
                "fish:bf322df2096a46f18c579d0baa36f41d",
                "fish:933563129e564b19a115bedd57b7406a",
                "fish:536d3a5e000945adb7038665781a4aca",
                "fish:e3cd384158934cc9a01029cd7d278634",
                "fish:79d0bd3e4e5444b18f7b6d89b5927bf1",
                "fish:9a9cf47702da476aa4629e2506d4a857",
            ],
        )
        self.assertEqual([voice.label for voice in voices[:6]], ["Selene", "Adrian", "Sarah", "Ethan", "Laura", "Jordan"])
        self.assertEqual(voices[-1].label, "Hannah")
        self.assertIn("default", voices[0].tags)
        self.assertIn("official", voices[1].tags)
        self.assertIn("starter", voices[-1].tags)

    def test_fish_generate_preview_prefers_sample_audio_from_voice_metadata(self):
        provider = FishAudioProvider()
        provider._client = SimpleNamespace(
            voices=SimpleNamespace(
                get=lambda _voice_id: SimpleNamespace(
                    samples=[SimpleNamespace(audio="https://example.com/sample.mp3")]
                )
            )
        )

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            @staticmethod
            def read():
                return b"sample-mp3"

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch("app.tts.fish_provider.urlopen", return_value=_FakeResponse()):
                preview_path = provider.generate_preview("fish-voice", "hello world", output_dir)

            self.assertEqual(preview_path.name, "preview_source_fish-voice.mp3")
            self.assertEqual(preview_path.read_bytes(), b"sample-mp3")

    def test_fish_generate_preview_does_not_fallback_to_paid_tts_without_sample(self):
        provider = FishAudioProvider()
        provider._client = SimpleNamespace(
            voices=SimpleNamespace(
                get=lambda _voice_id: SimpleNamespace(samples=[])
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with self.assertRaises(FileNotFoundError):
                provider.generate_preview("fish-voice", "hello world", output_dir)

    def test_upload_clone_preview_to_r2_uses_user_scoped_key(self):
        class _FakeR2:
            enabled = True

            def __init__(self):
                self.uploads = []

            def upload_file(self, local_path, r2_key, content_type=None):
                self.uploads.append((Path(local_path).name, r2_key, content_type))
                return r2_key

        fake_r2 = _FakeR2()

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "tts_previews"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached_preview = cache_dir / "fish_voice-1.wav"
            cached_preview.write_bytes(b"preview")

            with patch("app.api.routes.tts.settings.cache_dir", Path(temp_dir)):
                with patch("app.api.routes.tts.settings.use_r2_storage", True):
                    with patch("app.api.services.r2_storage.r2_storage", fake_r2):
                        tts_routes._maybe_upload_clone_preview_to_r2(
                            user_id=7,
                            fish_voice_id="voice-1",
                        )

        self.assertEqual(
            fake_r2.uploads,
            [("fish_voice-1.wav", "users/7/tts/fish/voice-1/preview.wav", None)],
        )

    def test_fish_list_voices_includes_public_tts_models_and_private_clones(self):
        captured = {}
        public_voice = SimpleNamespace(
            _id="public-1",
            type="tts",
            title="Public Voice",
            visibility="public",
            languages=["en"],
            task_count=42,
            tags=["featured"],
            author=SimpleNamespace(nickname="Fish"),
        )
        private_voice = SimpleNamespace(
            _id="private-1",
            type="tts",
            title="Private Voice",
            visibility="private",
        )
        svc_voice = SimpleNamespace(
            _id="svc-1",
            type="svc",
            title="SVC Voice",
            visibility="public",
        )

        class _FakeVoicesApi:
            @staticmethod
            def list(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(items=[public_voice, private_voice, svc_voice])

        provider = FishAudioProvider()
        provider._client = SimpleNamespace(voices=_FakeVoicesApi())
        clone_entry = VoiceCatalogEntry(
            voice_key="fish:clone-1",
            provider="fish",
            voice_id="clone-1",
            label="My Clone",
            is_cloned=True,
            owner_user_id=7,
            tags=["cloned", "private"],
        )

        with patch.object(provider, "_get_official_default_voices", return_value=[]):
            with patch.object(provider, "_get_user_cloned_voices", return_value=[clone_entry]):
                with patch("app.tts.fish_provider.settings.fish_preset_voice_ids", ""):
                    with patch("app.tts.fish_provider.settings.fish_public_voices_enabled", True):
                        with patch("app.tts.fish_provider.settings.fish_public_voice_page_size", 10):
                            with patch("app.tts.fish_provider.settings.fish_public_voice_page_number", 1):
                                with patch("app.tts.fish_provider.settings.fish_public_voice_sort_by", "score"):
                                    with patch("app.tts.fish_provider.settings.fish_public_voice_cache_seconds", 0):
                                        voices = provider.list_voices(user_id=7)

        self.assertEqual(captured["page_size"], 10)
        self.assertEqual(captured["page_number"], 1)
        self.assertEqual(captured["sort_by"], "score")
        self.assertEqual(captured["type"], "tts")
        self.assertEqual([voice.voice_key for voice in voices], ["fish:public-1", "fish:clone-1"])
        self.assertEqual(voices[0].label, "Public Voice - Fish")
        self.assertEqual(voices[0].grade, "42 uses")
        self.assertIn("public", voices[0].tags)
        self.assertIn("featured", voices[0].tags)

    def test_fish_default_voice_omits_reference_id(self):
        captured = {}

        class _FakeTTS:
            @staticmethod
            def convert(**kwargs):
                captured.update(kwargs)
                return b"mp3-bytes"

        provider = FishAudioProvider()
        provider._client = SimpleNamespace(tts=_FakeTTS())

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch("app.tts.fish_provider.subprocess.run"):
                provider.generate("hello world", "__default__", output_dir)

        self.assertEqual(captured["model"], "s2-pro")
        self.assertNotIn("reference_id", captured)

    def test_fish_generate_uses_configured_model(self):
        captured = {}

        class _FakeTTS:
            @staticmethod
            def convert(**kwargs):
                captured.update(kwargs)
                return b"mp3-bytes"

        provider = FishAudioProvider()
        provider._client = SimpleNamespace(tts=_FakeTTS())

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            mp3_path = output_dir / "narration_raw.mp3"
            with patch("app.tts.fish_provider.subprocess.run"):
                result = provider.generate("hello world", "fish-voice", output_dir)

            self.assertEqual(captured["model"], "s2-pro")
            self.assertEqual(captured["reference_id"], "fish-voice")
            self.assertEqual(result.audio_path, output_dir / "narration_raw.wav")
            self.assertFalse(mp3_path.exists())
