"""Claude backends for the scene planner.

Two ways to use Claude:

- ``anthropic``: the Anthropic API, billed to an API key (``ANTHROPIC_API_KEY``).
- ``claude-code``: shells out to the locally installed Claude Code CLI
  (``claude -p``), which runs on whatever plan that CLI is logged into, such as a
  Claude Pro/Max subscription. No API key needed; run ``claude`` once to log in.

Both take OpenAI-style message lists ({"role": ..., "content": ...}) so the
planner can call them the same way it calls Ollama and OpenRouter.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


class ClaudeError(RuntimeError):
    """Raised when a Claude backend cannot produce a usable response."""


def _split_system(messages: List[dict]) -> Tuple[str, List[dict]]:
    """Separate system messages from the conversation turns."""
    system_parts = []
    turns = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            turns.append({"role": role, "content": content})
    return "\n\n".join(system_parts), turns


class AnthropicChat:
    """Calls Claude through the official Anthropic Python SDK."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 effort: Optional[str] = None, timeout: float = 300.0):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - depends on install
            raise ClaudeError(
                "The 'anthropic' package is not installed. Run: pip install anthropic"
            ) from e

        self._anthropic = anthropic
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
        # or an `ant auth login` profile.
        self.client = anthropic.Anthropic(api_key=api_key or None, timeout=timeout)
        self.model = model or os.getenv("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.effort = effort or os.getenv("ANTHROPIC_EFFORT") or "medium"

    def chat(self, messages: List[dict], max_tokens: int = 16000) -> str:
        anthropic = self._anthropic
        system, turns = _split_system(messages)
        if not turns:
            raise ClaudeError("No user message to send to Claude")

        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=turns,
                output_config={"effort": self.effort},
                # If the model declines, the API retries on a fallback model
                # within the same call instead of returning an empty answer.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise ClaudeError(
                "Anthropic API rejected the key. Check ANTHROPIC_API_KEY."
            ) from e
        except anthropic.NotFoundError as e:
            raise ClaudeError(f"Unknown Claude model '{self.model}'.") from e
        except anthropic.RateLimitError as e:
            raise ClaudeError("Anthropic API rate limit hit. Try again shortly.") from e
        except anthropic.APIStatusError as e:
            raise ClaudeError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError("Could not reach the Anthropic API.") from e

        if response.stop_reason == "refusal":
            raise ClaudeError("Claude declined to write this script. Try a different topic.")

        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ClaudeError(f"Claude returned no text (stop_reason={response.stop_reason}).")
        return text


class ClaudeCodeChat:
    """Calls Claude through the Claude Code CLI in headless mode (``claude -p``).

    Uses the CLI's own login, so a Claude subscription works without an API key.
    Tools, MCP servers, and project/user settings are disabled so the call is a
    plain text completion.
    """

    def __init__(self, executable: Optional[str] = None, model: Optional[str] = None,
                 timeout: float = 300.0):
        self.executable = executable or os.getenv("CLAUDE_CODE_PATH") or "claude"
        if shutil.which(self.executable) is None:
            raise ClaudeError(
                f"Claude Code CLI not found ('{self.executable}'). Install it from "
                "https://claude.com/claude-code and run `claude` once to log in, "
                "or set CLAUDE_CODE_PATH."
            )
        self.model = model or os.getenv("CLAUDE_CODE_MODEL") or None
        self.timeout = timeout

    @staticmethod
    def _render_turns(turns: List[dict]) -> str:
        if len(turns) == 1:
            return turns[0]["content"]
        rendered = []
        for turn in turns:
            label = "User" if turn["role"] == "user" else "Assistant"
            rendered.append(f"{label}:\n{turn['content']}")
        rendered.append("Reply as the assistant to the last user message.")
        return "\n\n".join(rendered)

    def chat(self, messages: List[dict], max_tokens: int = 16000) -> str:
        system, turns = _split_system(messages)
        if not turns:
            raise ClaudeError("No user message to send to Claude")

        cmd = [
            self.executable, "-p",
            "--output-format", "json",
            "--tools", "",
            "--no-session-persistence",
            "--setting-sources", "",
            "--strict-mcp-config",
        ]
        if system:
            cmd += ["--system-prompt", system]
        if self.model:
            cmd += ["--model", self.model]

        env = dict(os.environ)
        # An API key in the environment would take precedence over the CLI's
        # subscription login; this backend exists to use the login.
        env.pop("ANTHROPIC_API_KEY", None)

        # Run outside the project so no CLAUDE.md or project settings are loaded.
        with tempfile.TemporaryDirectory(prefix="claude-llm-") as workdir:
            try:
                proc = subprocess.run(
                    cmd,
                    input=self._render_turns(turns),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=workdir,
                    env=env,
                )
            except subprocess.TimeoutExpired as e:
                raise ClaudeError(f"Claude Code timed out after {self.timeout:.0f}s") from e

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise ClaudeError(
                f"Claude Code failed (exit {proc.returncode}). If you haven't yet, run "
                f"`claude` once to log in. Output: {detail}"
            )

        if data.get("is_error") or proc.returncode != 0:
            detail = str(data.get("result") or data.get("subtype") or "unknown error")[:500]
            raise ClaudeError(f"Claude Code returned an error: {detail}")

        text = data.get("result") or ""
        if not text.strip():
            raise ClaudeError("Claude Code returned an empty response.")
        return text
