"""Out-of-the-box setup fixes: provider defaults, Kokoro languages, retries, credits,
queue position and the editor's real video length."""

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── 1. YouTube publishing is enabled by default ───────────────


def test_youtube_is_enabled_by_default(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("SOCIAL_ENABLED_PROVIDERS", raising=False)
    assert Settings(_env_file=None).social_enabled_providers == "youtube"
    assert Settings(_env_file=str(ROOT / ".env.example")).social_enabled_providers == "youtube"


def test_blank_social_providers_line_falls_back_to_youtube(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("SOCIAL_ENABLED_PROVIDERS", "")
    assert Settings(_env_file=None).social_enabled_providers == "youtube"


# ── 2. A Fish key is enough to enable Fish voices ─────────────


@pytest.mark.parametrize("listed,key,expected", [
    ("kokoro", "fish-key", ["kokoro", "fish"]),
    ("kokoro", "", ["kokoro"]),
    ("fish,kokoro", "fish-key", ["fish", "kokoro"]),
    ("kokoro,chatterbox", "fish-key", ["kokoro", "chatterbox", "fish"]),
])
def test_fish_auto_enabled_when_key_set(monkeypatch, listed, key, expected):
    from app.tts import registry

    monkeypatch.setenv("TTS_ENABLED_PROVIDERS", listed)
    monkeypatch.setattr(registry.settings, "fish_audio_api_key", key)
    assert registry.get_configured_provider_names() == expected


# ── 3. Kokoro: one pipeline per voice language ────────────────

tts_generator = pytest.importorskip("tts_generator")


@pytest.mark.parametrize("voice,expected", [
    ("af_heart", "a"), ("am_fenrir", "a"), ("bf_emma", "b"), ("bm_george", "b"),
    ("ff_siwis", "f"), ("jf_alpha", "j"), ("jm_kumo", "j"), ("zf_xiaobei", "z"),
    ("ef_dora", "e"), ("if_sara", "i"), ("im_nicola", "i"), ("pf_dora", "p"),
    ("hf_alpha", "h"), ("hm_omega", "h"), ("custom", "a"), ("", "a"), (None, "a"),
])
def test_kokoro_lang_for_voice(voice, expected):
    assert tts_generator.kokoro_lang_for_voice(voice) == expected


@pytest.fixture
def fake_kokoro(monkeypatch):
    """A fake `kokoro` module whose KPipeline records how it was built (no model load)."""
    created = []

    class FakePipeline:
        def __init__(self, lang_code, model=True, **kwargs):
            self.lang_code = lang_code
            self.model = model if model is not True else object()
            self.passed_model = model
            created.append(self)

    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KPipeline=FakePipeline))
    monkeypatch.setattr(tts_generator, "_kokoro_pipelines", {})
    return created


def test_kokoro_pipeline_follows_the_voice_language(fake_kokoro):
    gen = tts_generator.TTSGenerator(tts_engine="kokoro", kokoro_voice="ff_siwis", kokoro_lang="a")
    french = gen._kokoro_pipeline
    assert french.lang_code == "f"
    assert gen.whisper_language == "fr"

    gen.kokoro_voice = "af_heart"  # what KokoroProvider does per request
    english = gen._kokoro_pipeline
    assert english.lang_code == "a"
    assert gen.whisper_language == "en"

    gen.kokoro_voice = "ff_siwis"
    assert gen._kokoro_pipeline is french  # cached per language
    # Shared across generators in the process, one per language
    other = tts_generator.TTSGenerator(tts_engine="kokoro", kokoro_voice="ff_siwis")
    assert other._kokoro_pipeline is french
    assert [p.lang_code for p in fake_kokoro] == ["f", "a"]
    # Later languages reuse the first pipeline's model weights
    assert english.passed_model is french.model


def test_kokoro_unknown_voice_uses_kokoro_lang(fake_kokoro):
    gen = tts_generator.TTSGenerator(tts_engine="kokoro", kokoro_voice="my_voice.pt", kokoro_lang="b")
    gen._init_kokoro()
    assert [p.lang_code for p in fake_kokoro] == ["b"]


def test_whisper_uses_cuda_when_available():
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    with patch.dict(sys.modules, {"torch": fake_torch}):
        assert tts_generator._whisper_device() == ("cuda", "float16")
    fake_torch.cuda.is_available = lambda: False
    with patch.dict(sys.modules, {"torch": fake_torch}):
        assert tts_generator._whisper_device() == ("cpu", "int8")


# ── 6. Retry after a failed render ────────────────────────────

vs = pytest.importorskip("app.api.services.video_service", reason="API dependencies not installed")


def _session(**overrides):
    from app.api.services.session_store import VideoSession

    data = dict(
        session_id="abcdef123456", created_at="2026-01-01T00:00:00", status="failed",
        title="T", duration_seconds=30, loop_mode=False, subtitles_enabled=True,
        orientation="portrait", input_type="prompt", user_id=1,
        clips=[{"index": 0, "video_id": "1"}],
        error_message="Video generation failed.", progress_percent=40,
        current_step="Rendering", job_id="old-job",
    )
    data.update(overrides)
    return VideoSession(**data)


def _service():
    svc = vs.VideoService.__new__(vs.VideoService)
    svc.redis = MagicMock()
    svc.redis.set.return_value = True
    svc.redis.get.return_value = None
    svc.queue = MagicMock()
    svc.queue.enqueue.return_value = SimpleNamespace(id="new-job")
    return svc


def test_failed_render_with_clips_can_be_retried(monkeypatch):
    monkeypatch.setattr(vs.session_store, "update", lambda s: s)
    session = _session()
    assert vs.can_start_generation(session)

    job_id = _service().queue_video_generation(session)

    assert job_id == "new-job"
    assert session.status == "generating"
    assert session.error_message is None
    assert session.progress_percent == 0
    assert session.job_id == "new-job"


def test_failed_session_without_clips_still_rejected(monkeypatch):
    monkeypatch.setattr(vs.session_store, "update", lambda s: s)
    session = _session(clips=[])
    assert not vs.can_start_generation(session)
    with pytest.raises(ValueError):
        _service().queue_video_generation(session)


def test_draft_session_cannot_generate():
    assert not vs.can_start_generation(_session(status="script_ready"))
    assert vs.can_start_generation(_session(status="clips_ready"))


# ── 7. Credits follow clip swaps ──────────────────────────────


def test_write_credits_for_clips(tmp_path):
    from license_tracker import write_credits_for_clips

    output = tmp_path / "video.mp4"
    clips = [
        {"index": 0, "author": "Ann", "author_url": "https://pexels.com/v/1", "provider": "Pexels"},
        {"index": 1, "author": "Bob", "author_url": "https://pixabay.com/v/2", "provider": "pixabay"},
        {"index": 2, "author": "Ann", "author_url": "https://pexels.com/v/1", "provider": "Pexels",
         "loop_copy": True},
    ]
    txt, js = write_credits_for_clips(clips, output)

    assert txt == tmp_path / "video_credits.txt"
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["total_assets"] == 2
    assert [(a["author"], a["provider"], a["license_name"]) for a in data["assets"]] == [
        ("Ann", "Pexels", "Pexels License"), ("Bob", "Pixabay", "Pixabay License"),
    ]
    text = txt.read_text(encoding="utf-8")
    assert "Author: Bob" in text and "https://pixabay.com/v/2" in text


def test_editor_rewrites_credits_from_current_clips(tmp_path):
    es = pytest.importorskip("app.api.services.editor_service")
    output = tmp_path / "out.mp4"
    (tmp_path / "out_credits.txt").write_text("stale: Old Author", encoding="utf-8")
    project = {"clips": [{"index": 0, "author": "New Author",
                          "author_url": "https://pexels.com/v/9", "provider": "Pexels"}]}

    es.EditorService._rewrite_credits(project, output)

    text = (tmp_path / "out_credits.txt").read_text(encoding="utf-8")
    assert "New Author" in text and "Old Author" not in text
    data = json.loads((tmp_path / "out_credits.json").read_text(encoding="utf-8"))
    assert data["assets"][0]["video_url"] == "https://pexels.com/v/9"


# ── 8. Queue position reaches the client ──────────────────────


def test_status_route_passes_queue_position(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import videos

    app = FastAPI()
    app.include_router(videos.router, prefix="/api")
    app.dependency_overrides[videos.get_current_user] = lambda: SimpleNamespace(id=1)
    monkeypatch.setattr(videos, "get_owned_session",
                        lambda sid, user: SimpleNamespace(progress_percent=0, current_step=None))
    monkeypatch.setattr(videos.video_service, "get_job_status", lambda s: {
        "job_id": "j1", "status": "queued", "queue_position": 2, "queue_length": 3,
    })

    body = TestClient(app).get("/api/sessions/abc/status").json()
    assert body["queue_position"] == 2
    assert body["queue_length"] == 3


# ── 10. Editor reports the rendered length, not the planned one ──


def test_editor_duration_is_the_rendered_length(tmp_path, monkeypatch):
    es = pytest.importorskip("app.api.services.editor_service")
    svc = es.EditorService()
    (tmp_path / "narration.wav").write_bytes(b"x")
    output = tmp_path / "final.mp4"
    project = {
        "duration_seconds": 10.0, "outro_duration": 1.0, "loop_mode": False,
        "transition_duration": 0.5, "orientation": "portrait",
        "audio": {"narration_file": "narration.wav"}, "clips": [],
    }
    probes = {output: 14.175, tmp_path / "narration.wav": 13.2}
    monkeypatch.setattr(svc, "_probe_duration", lambda p: probes.get(Path(p)))

    # Output not on disk (e.g. moved to R2): narration + outro
    assert svc.rendered_duration(tmp_path, project, output) == pytest.approx(14.2)
    output.write_bytes(b"x")
    assert svc.rendered_duration(tmp_path, project, output) == pytest.approx(14.175)

    data = svc.get_editor_data(tmp_path, project, "/stream", output_path=output)
    assert data["duration_seconds"] == pytest.approx(14.175)


# ── 14. Kokoro pacing matches its real speaking rate ──────────


def test_kokoro_words_per_second_is_measured_rate(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "words_per_second", None)
    monkeypatch.setattr(config.settings, "kokoro_speed", 1.0)
    assert config.get_words_per_second("kokoro") == pytest.approx(2.5)
    monkeypatch.setattr(config.settings, "kokoro_speed", 1.2)
    assert config.get_words_per_second("kokoro") == pytest.approx(3.0)
    monkeypatch.setattr(config.settings, "fish_audio_words_per_second", 3.0)
    assert config.get_words_per_second("fish") == pytest.approx(3.0)
    assert config.get_words_per_second("chatterbox") == pytest.approx(2.5)
    # Explicit override still wins
    monkeypatch.setattr(config.settings, "words_per_second", 2.8)
    assert config.get_words_per_second("kokoro") == pytest.approx(2.8)


def test_cli_kokoro_words_per_second(monkeypatch):
    gv = pytest.importorskip("generate_video")
    monkeypatch.delenv("WORDS_PER_SECOND", raising=False)
    monkeypatch.setenv("TTS_ENGINE", "kokoro")
    monkeypatch.setenv("KOKORO_SPEED", "1.0")
    assert gv._cli_words_per_second() == pytest.approx(2.5)
    monkeypatch.setenv("WORDS_PER_SECOND", "3.3")
    assert gv._cli_words_per_second() == pytest.approx(3.3)


# ── 15. Re-captioning without a style keeps the current style ─


def _completed_session(tmp_path, **overrides):
    return _session(status="completed", output_path=str(tmp_path / "out.mp4"),
                    project_dir_path=str(tmp_path), **overrides)


def _regen(monkeypatch, tmp_path, session, body):
    from app.api.services import editor_service as es

    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    project = {"subtitles": {"style": "minimal"}}
    seen = {}
    monkeypatch.setattr(vs, "_find_project_dir", lambda s: tmp_path)
    monkeypatch.setattr(vs.session_store, "update", lambda s: s)
    monkeypatch.setattr(es.editor_service, "load_project", lambda d: project)
    monkeypatch.setattr(es.editor_service, "render_changes",
                        lambda *a, subtitles=None, **k: seen.update(subtitles))
    vs.regenerate_subtitles(session, body)
    return seen["style"]


def test_subtitle_update_keeps_session_style(monkeypatch, tmp_path):
    from app.api.routes.videos import UpdateSubtitlesRequest

    body = UpdateSubtitlesRequest(color="#FF0000").model_dump(exclude_none=True)
    assert "style" not in body
    session = _completed_session(tmp_path, subtitle_style="tiktok")
    assert _regen(monkeypatch, tmp_path, session, body) == "tiktok"
    assert session.subtitle_style == "tiktok"


def test_subtitle_update_falls_back_to_project_style(monkeypatch, tmp_path):
    session = _completed_session(tmp_path, subtitle_style=None)
    assert _regen(monkeypatch, tmp_path, session, {"position": "top"}) == "minimal"
    session = _completed_session(tmp_path, subtitle_style="tiktok")
    assert _regen(monkeypatch, tmp_path, session, {"style": "youtube"}) == "youtube"
