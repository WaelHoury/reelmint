"""Whisper language selection and Piper default tests (no TTS deps needed)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("numpy")

import tts_generator  # noqa: E402
from tts_generator import _resolve_whisper_language, whisper_language_for_kokoro  # noqa: E402


@pytest.mark.parametrize("code,expected", [
    ("a", "en"), ("b", "en"), ("f", "fr"), ("j", "ja"), ("e", "es"), ("i", "it"),
    ("p", "pt"), ("h", "hi"), ("z", "zh"), ("ff_siwis", "fr"), ("af_heart", "en"),
    ("x", None), ("", None), (None, None), ("english", None),
])
def test_whisper_language_for_kokoro(code, expected):
    assert whisper_language_for_kokoro(code) == expected


def test_resolve_language(monkeypatch):
    monkeypatch.delenv("WHISPER_LANGUAGE", raising=False)
    assert _resolve_whisper_language(None) is None  # auto-detect
    assert _resolve_whisper_language("FR") == "fr"
    assert _resolve_whisper_language("auto") is None
    monkeypatch.setenv("WHISPER_LANGUAGE", "de")
    assert _resolve_whisper_language(None) == "de"
    assert _resolve_whisper_language("en") == "en"


def test_tts_generator_whisper_language_and_piper_default():
    gen = tts_generator.TTSGenerator(tts_engine="kokoro", kokoro_voice="ff_siwis")
    assert gen.whisper_language == "fr"
    expected = "piper.exe" if os.name == "nt" else "piper"
    assert tts_generator.DEFAULT_PIPER_EXECUTABLE == expected
    assert gen.piper == expected


def test_long_espeak_path_is_redirected_to_short_copy(tmp_path, monkeypatch):
    import sys, types
    import tts_generator
    deep = tmp_path / ("d" * 180) / "espeakng_loader"
    (deep / "espeak-ng-data").mkdir(parents=True)
    (deep / "espeak-ng-data" / "phontab").write_bytes(b"x")
    (deep / "libespeak-ng.dylib").write_bytes(b"lib")
    fake = types.SimpleNamespace(get_data_path=lambda: str(deep / "espeak-ng-data"),
                                 get_library_path=lambda: str(deep / "libespeak-ng.dylib"))
    monkeypatch.setitem(sys.modules, "espeakng_loader", fake)
    monkeypatch.setattr(tts_generator.tempfile, "gettempdir", lambda: str(tmp_path / "t"))
    short = tts_generator.ensure_short_espeak_path()
    assert short is not None
    assert len(fake.get_data_path()) <= tts_generator.ESPEAK_MAX_DATA_PATH
    assert (tmp_path / "t" / "reelmint-espeak" / "espeak-ng-data" / "phontab").exists()


def test_short_espeak_path_left_alone(monkeypatch):
    import sys, types
    import tts_generator
    fake = types.SimpleNamespace(get_data_path=lambda: "/short/espeak-ng-data",
                                 get_library_path=lambda: "/short/libespeak-ng.dylib")
    monkeypatch.setitem(sys.modules, "espeakng_loader", fake)
    assert tts_generator.ensure_short_espeak_path() is None
