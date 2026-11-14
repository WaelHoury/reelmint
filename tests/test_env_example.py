"""A fresh clone runs `cp .env.example .env`; the app must start with that file as-is."""

from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_env_example_loads_cleanly():
    settings = Settings(_env_file=str(ROOT / ".env.example"))
    assert settings.llm_provider in ("ollama", "openrouter", "anthropic", "claude-code")
    # Blank values in the template mean "use the default", not a parse error
    assert settings.words_per_second is None or settings.words_per_second > 0


def test_env_example_has_no_inline_comment_on_empty_values():
    """Docker Compose reads `KEY=   # note` as the value "# note", unlike python-dotenv.

    That crashed every container in `docker compose up`, so comments for empty
    settings must sit on their own line.
    """
    import re
    bad = [line for line in (ROOT / ".env.example").read_text().splitlines()
           if re.match(r"^[A-Z0-9_]+=\s+#", line)]
    assert not bad, f"move these comments onto their own line: {bad}"
