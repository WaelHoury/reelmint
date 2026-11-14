"""Tests for the Claude backends (no network, no real Claude CLI needed)."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_llm import ClaudeCodeChat, ClaudeError, _split_system  # noqa: E402


def _fake_cli(tmp_path: Path, payload: dict, exit_code: int = 0) -> Path:
    """Write a fake `claude` executable that records its args/stdin and prints payload."""
    record = tmp_path / "record.json"
    script = tmp_path / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
        f"'has_api_key': 'ANTHROPIC_API_KEY' in os.environ, 'cwd': os.getcwd()}}, "
        f"open({str(record)!r}, 'w'))\n"
        f"print({json.dumps(json.dumps(payload))})\n"
        f"sys.exit({exit_code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _recorded(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "record.json").read_text())


def test_split_system_separates_roles():
    system, turns = _split_system([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "plan a video"},
    ])
    assert system == "be brief"
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]


def test_claude_code_returns_result_and_disables_tools(tmp_path, monkeypatch):
    cli = _fake_cli(tmp_path, {"type": "result", "is_error": False, "result": '{"clips": []}'})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")

    chat = ClaudeCodeChat(executable=str(cli), model="sonnet")
    out = chat.chat([
        {"role": "system", "content": "You write video plans."},
        {"role": "user", "content": "Topic: octopuses"},
    ])

    assert out == '{"clips": []}'
    rec = _recorded(tmp_path)
    argv = rec["argv"]
    assert argv[0] == "-p"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == "You write video plans."
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert rec["stdin"] == "Topic: octopuses"
    # Subscription login must win over any API key in the environment.
    assert rec["has_api_key"] is False
    # Runs outside the project so its CLAUDE.md is not loaded.
    assert Path(rec["cwd"]).resolve() != Path.cwd().resolve()


def test_claude_code_error_is_reported(tmp_path):
    cli = _fake_cli(tmp_path, {"type": "result", "is_error": True, "result": "Not logged in"},
                    exit_code=1)
    with pytest.raises(ClaudeError, match="Not logged in"):
        ClaudeCodeChat(executable=str(cli)).chat([{"role": "user", "content": "x"}])


def test_claude_code_non_json_output(tmp_path):
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\necho 'Invalid API key' >&2\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(ClaudeError, match="log in"):
        ClaudeCodeChat(executable=str(script)).chat([{"role": "user", "content": "x"}])


def test_claude_code_missing_cli():
    with pytest.raises(ClaudeError, match="not found"):
        ClaudeCodeChat(executable="definitely-not-a-real-claude-binary")


def test_multi_turn_is_rendered_as_transcript(tmp_path):
    cli = _fake_cli(tmp_path, {"is_error": False, "result": "ok"})
    ClaudeCodeChat(executable=str(cli)).chat([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "bad json"},
        {"role": "user", "content": "fix it"},
    ])
    stdin = _recorded(tmp_path)["stdin"]
    assert "User:\nfirst" in stdin and "Assistant:\nbad json" in stdin
    assert stdin.rstrip().endswith("last user message.")
