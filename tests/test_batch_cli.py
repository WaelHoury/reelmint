"""CLI --batch: batch file parsing and the one-at-a-time render loop."""

from pathlib import Path

import pytest

pytest.importorskip("pydantic")

from generate_video import batch_output_path, parse_batch_file, run_batch  # noqa: E402


def test_topics_one_per_line_ignoring_blanks_and_comments():
    text = "# my topics\nIs a hot dog a sandwich?\n\n   \n  # indented comment\nIs cereal soup?  \n"
    assert parse_batch_file(text) == [
        {"input_type": "prompt", "text": "Is a hot dog a sandwich?"},
        {"input_type": "prompt", "text": "Is cereal soup?"},
    ]


def test_scripts_separated_by_dashes():
    text = "First script line one.\nLine two.\n---\n\n# Not a comment in a script.\nSecond script.\n---\n\n---\n"
    assert parse_batch_file(text) == [
        {"input_type": "script", "text": "First script line one.\nLine two."},
        {"input_type": "script", "text": "# Not a comment in a script.\nSecond script."},
    ]


def test_dashes_inside_a_line_do_not_switch_to_script_mode():
    assert parse_batch_file("Topic --- with dashes\nAnother") == [
        {"input_type": "prompt", "text": "Topic --- with dashes"},
        {"input_type": "prompt", "text": "Another"},
    ]


def test_empty_file():
    assert parse_batch_file("# only comments\n\n") == []


def test_example_file_parses():
    path = Path(__file__).resolve().parent.parent / "examples" / "batch-topics.txt"
    entries = parse_batch_file(path.read_text())
    assert len(entries) == 3 and all(e["input_type"] == "prompt" for e in entries)


def test_output_path_numbering_and_sanitizing(tmp_path):
    assert batch_output_path(tmp_path, 3, 12, "Is a hot dog a sandwich?").name == "03-Is a hot dog a sandwich.mp4"
    assert batch_output_path(tmp_path, 7, 120, "a/b").name.startswith("007-")


def test_run_batch_continues_after_failure(tmp_path):
    rendered = []

    def fake_render(**kwargs):
        rendered.append(kwargs)
        if kwargs["prompt"] == "boom":
            raise RuntimeError("ffmpeg exploded")
        Path(kwargs["output_path"]).write_bytes(b"v")

    entries = [
        {"input_type": "prompt", "text": "first"},
        {"input_type": "prompt", "text": "boom"},
        {"input_type": "script", "text": "word " * 90},
    ]
    results = run_batch(entries, tmp_path / "out", duration_minutes=0.5, orientation="portrait", render=fake_render)

    assert [r["status"] for r in results] == ["done", "failed", "done"]
    assert results[1]["error"] == "ffmpeg exploded"
    assert [Path(k["output_path"]).name[:3] for k in rendered] == ["01-", "02-", "03-"]
    assert rendered[0]["duration_minutes"] == 0.5 and rendered[0]["script_text"] is None
    assert rendered[2]["prompt"] is None and rendered[2]["duration_minutes"] > 0
    assert (tmp_path / "out").is_dir()
