"""Unit tests for small helpers in the TTS and editor routes (no services needed)."""

import io

import pytest

tts = pytest.importorskip("app.api.routes.tts", reason="API dependencies not installed")
editor_service = pytest.importorskip("app.api.services.editor_service", reason="API dependencies not installed")

from fastapi import HTTPException  # noqa: E402


def test_copy_upload_capped_copies_small_file():
    src = io.BytesIO(b"x" * 3000)
    dst = io.BytesIO()
    assert tts._copy_upload_capped(src, dst, max_bytes=5000) == 3000
    assert dst.getvalue() == b"x" * 3000


def test_copy_upload_capped_aborts_with_413_once_over_cap(monkeypatch):
    monkeypatch.setattr(tts, "_UPLOAD_CHUNK_BYTES", 1024)
    src = io.BytesIO(b"x" * 10_000)
    dst = io.BytesIO()
    with pytest.raises(HTTPException) as exc:
        tts._copy_upload_capped(src, dst, max_bytes=4096)
    assert exc.value.status_code == 413
    assert len(dst.getvalue()) <= 4096
    # Stopped early instead of reading everything
    assert src.tell() < 10_000


@pytest.mark.parametrize("stdout", ["N/A", "", "   ", "abc"])
def test_parse_ffprobe_duration_unreadable_is_400(stdout):
    with pytest.raises(HTTPException) as exc:
        tts._parse_ffprobe_duration(0, stdout)
    assert exc.value.status_code == 400


def test_parse_ffprobe_duration_failed_probe_is_400():
    with pytest.raises(HTTPException) as exc:
        tts._parse_ffprobe_duration(1, "12.5")
    assert exc.value.status_code == 400


def test_parse_ffprobe_duration_ok():
    assert tts._parse_ffprobe_duration(0, "12.500000\n") == 12.5


def test_edit_lock_key_is_shared_by_session_and_library_paths():
    key = editor_service.video_edit_lock_key
    assert key("/app/output/abc.mp4") == key("output/abc.mp4")
    assert key("/app/output/abc.mp4") != key("/app/output/def.mp4")
