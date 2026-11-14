"""Tests that narration text can't inject ASS override tags."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from subtitle_generator import SubtitleGenerator, escape_ass_text  # noqa: E402

TRICKY = "Use {\\b1}braces{} and back\\slash \\N here"


def _dialogue_texts(path: Path):
    return [line.split(",", 9)[9] for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Dialogue:")]


def _assert_no_raw_override(text: str):
    # Every brace in narration must be escaped as \{ or \}
    for i, ch in enumerate(text):
        if ch in "{}":
            assert i > 0 and text[i - 1] == "\\", text


def test_escape_ass_text():
    out = escape_ass_text("a {b} c\\N d\nnext")
    assert out == "a \\{b\\} c\u29f5N d next"


def test_generate_ass_escapes(tmp_path):
    gen = SubtitleGenerator(orientation="landscape")
    out = gen.generate_ass([{"narration": TRICKY, "start_time": 0.0, "duration": 3.0}],
                           tmp_path / "s.ass")
    (text,) = _dialogue_texts(out)
    _assert_no_raw_override(text)
    assert "\\{\\b1\\}" not in text  # backslash in narration replaced, not kept


def test_phrase_ass_escapes(tmp_path):
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3}
             for i, w in enumerate(TRICKY.split())]
    out = SubtitleGenerator(orientation="landscape").generate_phrase_ass(words, tmp_path / "p.ass")
    texts = _dialogue_texts(out)
    assert texts
    for t in texts:
        _assert_no_raw_override(t)


def test_karaoke_ass_escapes_text_but_keeps_tags(tmp_path):
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3}
             for i, w in enumerate(TRICKY.split())]
    gen = SubtitleGenerator(orientation="portrait")
    out = gen.generate_karaoke_ass(words, tmp_path / "k.ass")
    texts = _dialogue_texts(out)
    # Generated colour override tags still present
    assert any("{\\c" in t for t in texts)
    for t in texts:
        stripped = t
        # remove our own generated tags, then nothing unescaped may remain
        import re
        stripped = re.sub(r"\{\\c&H[0-9A-Fa-f]+&?\}", "", stripped)
        _assert_no_raw_override(stripped)
