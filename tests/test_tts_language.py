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
    gen = tts_generator.TTSGenerator(tts_engine="kokoro", kokoro_lang="f")
    assert gen.whisper_language == "fr"
    expected = "piper.exe" if os.name == "nt" else "piper"
    assert tts_generator.DEFAULT_PIPER_EXECUTABLE == expected
    assert gen.piper == expected
