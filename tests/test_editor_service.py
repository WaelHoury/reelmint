"""Editor service: padding plan, word-timing preservation, validation, clip search, locks."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

es = pytest.importorskip("app.api.services.editor_service", reason="API dependencies not installed")

from subtitle_generator import SubtitleGenerator  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ── Padding plan (narration must never be cut) ────────────────


def test_plan_pads_untouched_clips_need_no_padding():
    # 3 x 4s with 0.5s crossfades = 11s, exactly the narration
    assert es.plan_clip_pads([4, 4, 4], [0, 0, 0], 0.5, 11.0) == [0, 0, 0]


def test_plan_pads_trimmed_clip_is_extended_by_its_trim():
    pads = es.plan_clip_pads([4, 2.5, 4], [0, 1.5, 0], 0.5, 11.0)
    assert pads == [0, 1.5, 0]


def test_plan_pads_shares_deficit_by_shortfall():
    pads = es.plan_clip_pads([3, 2, 4], [1, 2, 0], 0.5, 11.0)
    assert pads == [1.0, 2.0, 0]
    assert sum(e + p for e, p in zip([3, 2, 4], pads)) - 1.0 == pytest.approx(11.0)


def test_plan_pads_surplus_or_frame_rounding_is_not_padded():
    assert es.plan_clip_pads([4, 3, 4], [0, 1, 0], 0.5, 10.0) == [0, 0, 0]
    assert es.plan_clip_pads([4, 4, 3.95], [0, 0, 0.05], 0.5, 11.0) == [0, 0, 0]


def test_plan_pads_no_shortfall_goes_to_last_clip_and_min_clip_respected():
    assert es.plan_clip_pads([4, 4, 4], [0, 0, 0], 0.5, 12.0) == [0, 0, 1.0]
    pads = es.plan_clip_pads([0.3, 4], [3.7, 0], 0.5, 0, min_clip=0.6)
    assert pads[0] == pytest.approx(0.3)


# ── Subtitle entries -> word timestamps ───────────────────────


def _words():
    # Uneven ASR-like timing: three phrases ending in punctuation
    return [
        {"word": "Hello", "start": 0.10, "end": 0.40},
        {"word": "world.", "start": 0.55, "end": 1.20},
        {"word": "This", "start": 1.50, "end": 1.60},
        {"word": "is", "start": 1.62, "end": 1.70},
        {"word": "fine.", "start": 1.90, "end": 2.60},
        {"word": "Bye", "start": 3.00, "end": 3.40},
    ]


def test_unchanged_entries_keep_original_word_timing():
    svc = es.EditorService()
    words = _words()
    entries = svc.compute_subtitle_entries(words, "landscape")
    assert [e["text"] for e in entries] == ["Hello world.", "This is fine.", "Bye"]
    assert svc._entries_to_word_timestamps(entries, words, "landscape") == words


def test_only_edited_phrase_is_retimed():
    svc = es.EditorService()
    words = _words()
    entries = svc.compute_subtitle_entries(words, "landscape")
    entries[1] = {**entries[1], "text": "That was really fine."}
    out = svc._entries_to_word_timestamps(entries, words, "landscape")
    assert out[:2] == words[:2]
    assert out[-1] == words[-1]
    edited = out[2:-1]
    assert [w["word"] for w in edited] == ["That", "was", "really", "fine."]
    assert edited[0]["start"] == pytest.approx(1.5) and edited[-1]["end"] == pytest.approx(2.6)


def test_moved_phrase_keeps_relative_word_timing():
    svc = es.EditorService()
    words = _words()
    entries = svc.compute_subtitle_entries(words, "landscape")
    entries[0] = {**entries[0], "start_time": 1.10, "end_time": 2.20}  # shifted by 1s
    out = svc._entries_to_word_timestamps(entries, words, "landscape")
    hello, world = [w for w in out if w["word"] in ("Hello", "world.")]
    assert hello["start"] == pytest.approx(1.10) and hello["end"] == pytest.approx(1.40)
    assert world["start"] == pytest.approx(1.55) and world["end"] == pytest.approx(2.20)


def test_text_edit_with_same_word_count_keeps_word_rhythm():
    svc = es.EditorService()
    words = _words()
    entries = svc.compute_subtitle_entries(words, "landscape")
    entries[0] = {**entries[0], "text": "Hi planet."}
    out = svc._entries_to_word_timestamps(entries, words, "landscape")
    assert out[0] == {"word": "Hi", "start": 0.10, "end": 0.40}
    assert out[1] == {"word": "planet.", "start": 0.55, "end": 1.20}


def test_emptied_caption_drops_its_words():
    svc = es.EditorService()
    words = _words()
    entries = svc.compute_subtitle_entries(words, "landscape")
    entries[2] = {**entries[2], "text": "  "}
    out = svc._entries_to_word_timestamps(entries, words, "landscape")
    assert "Bye" not in [w["word"] for w in out]


# ── Colours and resolved subtitle settings ────────────────────


@pytest.mark.parametrize("value,expected", [
    ("#fff", "#FFFFFF"), ("#1a2B3c", "#1A2B3C"), ("1A2B3C", "#1A2B3C"), ("#11223344", "#112233"),
    ("rgb(255, 0, 16)", "#FF0010"), ("rgba(0,128,255,0.5)", "#0080FF"), ("RGBA(1, 2, 3, 1)", "#010203"),
])
def test_normalize_color_accepts_css_forms(value, expected):
    assert SubtitleGenerator.normalize_color(value) == expected


@pytest.mark.parametrize("value", ["", None, "red", "#12", "rgb(300,0,0)", "rgb(1,2)", "#GGGGGG"])
def test_normalize_color_rejects_garbage(value):
    with pytest.raises(ValueError):
        SubtitleGenerator.normalize_color(value)


def test_hex_to_ass_color_and_back():
    assert SubtitleGenerator.hex_to_ass_color("rgba(255, 128, 0, 0.3)") == "&H000080FF"
    assert SubtitleGenerator.ass_to_hex_color("&H000080FF") == "#FF8000"


def test_resolved_settings_follow_style_and_orientation():
    tiktok_land = SubtitleGenerator("tiktok", "landscape").resolved_settings()
    assert tiktok_land == {"font": "Montserrat-Bold", "font_size": 64, "color": "#FFFFFF",
                           "outline_color": "#000000", "position": "center"}
    # Portrait/square captions are karaoke, which has its own base style
    portrait = SubtitleGenerator("youtube", "portrait").resolved_settings()
    assert portrait["font"] == "Comic Sans MS" and portrait["position"] == "center"


def test_karaoke_applies_user_overrides(tmp_path):
    gen = SubtitleGenerator("tiktok", "portrait", custom_overrides={
        "font": "Impact", "font_size": 60, "color": "#00FF00", "position": "bottom"})
    assert gen.resolved_settings() == {"font": "Impact", "font_size": 60, "color": "#00FF00",
                                       "outline_color": "#000000", "position": "bottom"}
    out = gen.generate_karaoke_ass(_words(), tmp_path / "k.ass")
    style = [l for l in out.read_text().splitlines() if l.startswith("Style:")][0]
    assert style.startswith("Style: Karaoke,Impact,60,&H0000FF00,")
    assert ",2,10,10,120,1" in style  # bottom alignment with a margin


def test_subtitle_state_for_legacy_project_uses_preset_not_old_defaults():
    svc = es.EditorService()
    project = {"orientation": "landscape", "subtitles": {
        "style": "tiktok", "font": "Arial", "font_size": 52, "color": "#FFFFFF",
        "outline_color": "#000000", "position": "bottom"}}
    state = svc.subtitle_state(project)
    assert state["overrides"] == {}
    assert state["resolved"]["font"] == "Montserrat-Bold" and state["resolved"]["position"] == "center"
    assert set(state["presets"]) == {"youtube", "tiktok", "minimal"}

    project["subtitles"]["color"] = "rgba(255,0,0,1)"  # a real legacy choice
    assert svc.subtitle_state(project)["overrides"] == {"color": "#FF0000"}


# ── Request validation ────────────────────────────────────────


def test_schemas_reject_bad_trims_captions_and_colours():
    from pydantic import ValidationError
    from app.api.schemas.editor import EditorSubtitleSettings, ReplacedClip, SubtitleEntry

    with pytest.raises(ValidationError):
        ReplacedClip(index=0, trim_start=-1)
    with pytest.raises(ValidationError):
        ReplacedClip(index=-1)
    with pytest.raises(ValidationError, match="must be after"):
        SubtitleEntry(index=0, text="a", start_time=2.0, end_time=2.0)
    with pytest.raises(ValidationError, match="not a colour"):
        EditorSubtitleSettings(color="rgb(999,0,0)")
    with pytest.raises(ValidationError):
        EditorSubtitleSettings(font="Arial,Bold")
    s = EditorSubtitleSettings(color="rgba(255, 255, 255, 0.4)", outline_color=None)
    assert s.color == "#FFFFFF" and s.outline_color is None and s.font is None


@pytest.fixture
def editor_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import get_current_user
    from app.api.routes.editor import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


def test_render_route_returns_400_for_invalid_body(editor_client):
    resp = editor_client.put("/api/sessions/abc/editor/render", json={
        "subtitles": {"entries": [{"index": 0, "text": "x", "start_time": 3, "end_time": 1}]}})
    assert resp.status_code == 400
    assert "Caption 1: end time" in resp.json()["detail"]

    resp = editor_client.put("/api/sessions/abc/editor/render", json={
        "subtitles": {"color": None, "outline_color": "nope"}})
    assert resp.status_code == 400
    assert "not a colour" in resp.json()["detail"]


def test_render_errors_map_to_clear_statuses():
    from app.api.routes.editor import _render_error
    assert _render_error(es.EditorValidationError("bad trim")).status_code == 400
    err = _render_error(es.ClipReplacementError([(1, "download failed"), (3, "not found")]))
    assert err.status_code == 502
    assert "clip 2 (download failed)" in err.detail and "clip 4 (not found)" in err.detail
    assert _render_error(RuntimeError("boom")).detail == "Render failed"


def _project(n=3, loop=False):
    return {
        "loop_mode": loop,
        "clips": [{"index": i, "video_id": str(100 + i), "provider": "Pexels",
                   "duration_seconds": 4.0, "source_file": f"clips/clip_{i}.mp4",
                   "file": f"clips/clip_{i}.mp4", "trim_start": 0, "trim_end": 0}
                  for i in range(n)],
    }


def test_validate_clip_edits():
    svc = es.EditorService()
    svc.validate_clip_edits(_project(), [{"index": 1, "trim_start": 1.5, "trim_end": 2.0}])
    with pytest.raises(es.EditorValidationError, match="does not exist"):
        svc.validate_clip_edits(_project(), [{"index": 3}])
    with pytest.raises(es.EditorValidationError, match="must leave at least"):
        svc.validate_clip_edits(_project(), [{"index": 0, "trim_start": 2.0, "trim_end": 1.6}])
    with pytest.raises(es.EditorValidationError, match="loops seamlessly"):
        svc.validate_clip_edits(_project(loop=True), [{"index": 2, "video_id": "999"}])
    # Unchanged loop-copy entry is fine
    svc.validate_clip_edits(_project(loop=True), [{"index": 2, "video_id": "102"}])


def test_failed_replacement_reports_indices_and_changes_nothing(tmp_path, monkeypatch):
    svc = es.EditorService()
    project = _project()

    def fake_fetch(project_dir, project, idx, video_id, provider, media_type, orientation):
        if idx == 2:
            raise ValueError("Pexels video 555 not found")
        (project_dir / "clips").mkdir(exist_ok=True)
        (project_dir / f"clips/clip_{idx}_src_{video_id}.mp4").write_bytes(b"x")
        return {"source_file": f"clips/clip_{idx}_src_{video_id}.mp4", "video_id": video_id}

    monkeypatch.setattr(svc, "_fetch_replacement", fake_fetch)
    monkeypatch.setattr(svc, "_build_render_clips", lambda *a: pytest.fail("must not render"))
    with pytest.raises(es.ClipReplacementError) as exc:
        svc._reprocess_clips(tmp_path, project, [
            {"index": 0, "video_id": "444", "keywords": ["a"]},
            {"index": 2, "video_id": "555", "keywords": ["b"]},
        ], "landscape")
    assert exc.value.failed_indices == [2]
    assert "clip 3" in str(exc.value)
    assert project["clips"][0]["video_id"] == "100"  # untouched
    assert not (tmp_path / "clips/clip_0_src_444.mp4").exists()  # fetched file cleaned up


# ── Clip search ───────────────────────────────────────────────


class _Info(SimpleNamespace):
    pass


def _info(i, duration, provider="Pexels", media_type="video"):
    return _Info(id=i, duration=duration, provider=provider, media_type=media_type,
                 orientation="landscape", preview_url=f"p{i}", download_url=f"d{i}", url=f"u{i}",
                 author="a", width=1920, height=1080)


def test_search_clips_excludes_used_and_prefers_long_enough(monkeypatch):
    calls = []

    class FakeSearcher:
        pixabay = None
        enable_pixabay = False
        enable_image_fallback = False

        def search(self, keywords, min_duration=5.0, max_results=20, orientation="any", page=1):
            calls.append((tuple(keywords), min_duration, page))
            return [_info(1, 30), _info(2, 3), _info(3, 12), _info(4, 20)]

    import importlib
    vs = importlib.import_module("app.api.services.video_service")
    monkeypatch.setattr(vs, "build_media_searcher", lambda: FakeSearcher())
    out = es.EditorService().search_clips(
        ["ocean", "waves"], "landscape", count=6, min_duration=8.0,
        exclude=[{"video_id": "1", "provider": "Pexels"}, {"video_id": "4", "provider": "Pixabay"}])
    ids = [r["video_id"] for r in out]
    assert "1" not in ids          # already in the video
    assert "2" not in ids          # shorter than the 8s slot while longer ones exist
    assert ids == ["3", "4"]       # Pixabay 4 is a different clip than Pexels 4
    assert out[0]["provider"] == "Pexels" and out[0]["preview_url"] == "p3"
    assert calls[0] == (("ocean", "waves"), 5.0, 1)


# ── Lock renewal ──────────────────────────────────────────────


def test_project_lock_renews_ttl_during_long_operations(monkeypatch):
    import time
    from app.utils import helpers

    class FakeLock:
        def __init__(self):
            self.renewals = 0
            self.released = False

        def acquire(self, blocking=True):
            return True

        def reacquire(self):
            self.renewals += 1

        def release(self):
            self.released = True

    lock = FakeLock()
    monkeypatch.setattr(helpers, "_get_lock_redis", lambda: SimpleNamespace(lock=lambda *a, **k: lock))
    with helpers.project_lock("x", timeout=0.6):
        time.sleep(0.75)
    assert lock.renewals >= 2
    assert lock.released
    count = lock.renewals
    time.sleep(0.4)
    assert lock.renewals == count  # renewer stopped with the block


# ── Real ffmpeg: rebuild keeps narration + outro, trims derive from source ─


def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", *args], check=True)


def _dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return float(out)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_trim_rebuild_keeps_video_as_long_as_narration_plus_outro(tmp_path):
    svc = es.EditorService()
    (tmp_path / "clips").mkdir()
    for i in range(2):
        _ff("-f", "lavfi", "-i", "testsrc=s=160x90:r=30:d=3", "-c:v", "libx264", "-preset",
            "ultrafast", "-pix_fmt", "yuv420p", "-y", str(tmp_path / f"clips/clip_{i}.mp4"))
    _ff("-f", "lavfi", "-i", "sine=d=5.5", "-y", str(tmp_path / "narration.wav"))
    project = {
        "orientation": "landscape", "loop_mode": False, "transition_duration": 0.5,
        "transition_type": "crossfade", "outro_duration": 1.0, "fade_duration": 1.0,
        "audio": {"narration_file": "narration.wav"}, "output": {},
        "subtitles": {"enabled": False},
        "clips": [{"index": i, "video_id": str(i), "duration_seconds": 3.0, "file": f"clips/clip_{i}.mp4",
                   "source_file": f"clips/clip_{i}.mp4", "trimmed_file": None,
                   "trim_start": 0, "trim_end": 0} for i in range(2)],
    }
    svc.save_project(tmp_path, project)
    out = tmp_path / "out.mp4"
    source_bytes = (tmp_path / "clips/clip_0.mp4").read_bytes()

    for trims in ((0.0, 1.0), (0.5, 0.0)):  # second edit must not stack on the first
        project = svc.load_project(tmp_path)
        svc.render_changes(tmp_path, project, replaced_clips=[
            {"index": 0, "video_id": "0", "keywords": [], "trim_start": trims[0], "trim_end": trims[1]}],
            output_path=out)
        project = svc.load_project(tmp_path)
        clip = project["clips"][0]
        assert clip["source_file"] == "clips/clip_0.mp4" and clip["trimmed_file"] == "clips/clip_0_edit.mp4"
        assert clip["render_spec"]["effective"] == pytest.approx(3.0 - sum(trims))
        assert clip["pad_seconds"] == pytest.approx(sum(trims), abs=0.05)
        # 5.5s narration + 1s outro, nothing cut
        assert _dur(out) == pytest.approx(6.5, abs=0.15)
    assert (tmp_path / "clips/clip_0.mp4").read_bytes() == source_bytes
    assert project["render_clips"][-1] == "clips/clip_1_edit.mp4"  # outro on last clip
