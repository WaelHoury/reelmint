"""Script loading utilities for --script flag support."""

from pathlib import Path
from typing import NamedTuple, Optional


class ScriptInput(NamedTuple):
    """Loaded script with metadata."""
    text: str
    source: str  # 'file' or 'inline'
    file_path: Optional[Path]  # Only set if source == 'file'


def load_script(script_arg: str) -> ScriptInput:
    """Load script from file path or inline text.

    Args:
        script_arg: Either a file path or inline script text

    Returns:
        ScriptInput with loaded text and source type

    Raises:
        ValueError: If file path given but file is empty
        FileNotFoundError: If path looks like file but doesn't exist
    """
    path = Path(script_arg)

    # Check if it's a file path
    if path.exists() and path.is_file():
        text = path.read_text(encoding='utf-8').strip()
        if not text:
            raise ValueError(f"Script file is empty: {path}")
        return ScriptInput(text=text, source='file', file_path=path)

    # Check if it looks like a file path but doesn't exist
    # (has .txt extension or contains path separators)
    if script_arg.endswith('.txt') or '/' in script_arg or '\\' in script_arg:
        raise FileNotFoundError(f"Script file not found: {script_arg}")

    # Treat as inline text
    text = script_arg.strip()
    if not text:
        raise ValueError("Script text cannot be empty")

    return ScriptInput(text=text, source='inline', file_path=None)


def estimate_duration_from_script(script_text: str, words_per_second: float = 3.0) -> float:
    """Estimate video duration in minutes from script word count.

    Args:
        script_text: The narration script
        words_per_second: Speaking rate (default 3.0)

    Returns:
        Estimated duration in minutes
    """
    word_count = len(script_text.split())
    duration_seconds = word_count / words_per_second
    duration_minutes = duration_seconds / 60
    return duration_minutes
