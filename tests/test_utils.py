"""Tests for root utils.py helpers (filename sanitizing, FFmpeg escaping)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import escape_concat_path, escape_ffmpeg_filter_path, sanitize_filename  # noqa: E402


@pytest.mark.parametrize("raw,expected", [
    ("Is cereal soup?", "Is cereal soup"),
    ("Why it's [not] a soup; really: yes, no = maybe", "Why its not a soup really yes no maybe"),
    ('He said "hi"\\there', "He said hithere"),
    ("a/b\\c", "abc"),
    ("  lots   of\t\nspace  ", "lots of space"),
    ("...hidden", "hidden"),
    ("Don\u2019t panic", "Dont panic"),
    ("'\"[],;:=\\", "untitled"),
    ("", "untitled"),
])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_has_no_ffmpeg_specials_and_truncates():
    out = sanitize_filename("x'\"[],;:=\\{}" * 50, max_length=20)
    assert not set(out) & set("'\"[],;:=\\{}")
    assert len(sanitize_filename("word " * 100, max_length=20)) <= 20


def test_escape_concat_path():
    assert escape_concat_path("/tmp/it's.mp4") == "'/tmp/it'\\''s.mp4'"
    assert escape_concat_path("C:\\v\\a.mp4") == "'C:/v/a.mp4'"


def test_escape_ffmpeg_filter_path():
    assert escape_ffmpeg_filter_path("/tmp/a.ass") == "/tmp/a.ass"
    # ' is special at both levels:  '  -> \'  -> \\\'
    assert escape_ffmpeg_filter_path("/t/it's.ass") == r"/t/it\\\'s.ass"
    # : only at the option level:   :  -> \:  -> \\:
    assert escape_ffmpeg_filter_path("C:/x.ass") == r"C\\:/x.ass"
    # [ ] , ; only at the filtergraph level
    assert escape_ffmpeg_filter_path("/a [b], c;d.ass") == r"/a \[b\]\, c\;d.ass"
