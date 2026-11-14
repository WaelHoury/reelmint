"""Utility functions for video generation."""

import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Tuple
import os


# Characters that break filesystem paths, shell-ish contexts, or FFmpeg
# filter/concat syntax (e.g. ass='.../subtitles.ass', file '...').
_UNSAFE_FILENAME_CHARS = re.compile(r"""[<>:"'`|?*\\/\[\],;=%$!&{}#~^]""")


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string (e.g. a video title) for use in file/dir names.

    Removes path separators, filesystem-illegal characters, control chars and
    characters that break FFmpeg filter strings or concat lists
    (quotes, brackets, commas, semicolons, colons, '=' and backslashes).
    Whitespace is collapsed; the result stays human-readable.
    Falls back to 'untitled' if nothing remains.
    """
    name = unicodedata.normalize("NFKC", str(name or ""))
    # Typographic quotes behave like apostrophes for our purposes
    name = re.sub(r"[\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u00b4]", "", name)
    name = re.sub(r"\s+", " ", name)  # tabs/newlines -> space first
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = _UNSAFE_FILENAME_CHARS.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.lstrip(".-").strip()
    if len(name) > max_length:
        name = name[:max_length].rstrip()
    return name or "untitled"


def escape_concat_path(path) -> str:
    """Quote a path for an FFmpeg concat-demuxer list line: file '<path>'.

    Inside single quotes the only special char is the quote itself, which is
    written as '\\'' (close quote, escaped quote, reopen quote).
    """
    path_str = str(path).replace("\\", "/")
    return "'" + path_str.replace("'", "'\\''") + "'"


def escape_ffmpeg_filter_path(path) -> str:
    """Escape a path for use as a filter option value in a -vf/-filter_complex
    string, e.g. f"ass={escape_ffmpeg_filter_path(p)}" (no extra quotes).

    Applies both FFmpeg escaping levels: the option-value level (\\ ' :) and
    the filtergraph level (\\ ' [ ] , ;).
    """
    path_str = str(path).replace("\\", "/")
    level1 = re.sub(r"([\\':])", r"\\\1", path_str)
    return re.sub(r"([\\'\[\],;])", r"\\\1", level1)


def get_media_duration(file_path: Path, ffprobe_path: str = "ffprobe") -> float:
    """Get duration of video or audio file.

    Args:
        file_path: Path to media file
        ffprobe_path: Path to ffprobe executable

    Returns:
        Duration in seconds

    Raises:
        subprocess.CalledProcessError: If ffprobe fails
    """
    cmd = [
        ffprobe_path, '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(file_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def get_video_resolution(file_path: Path, ffprobe_path: str = "ffprobe") -> Tuple[int, int]:
    """Get video resolution.

    Args:
        file_path: Path to video file
        ffprobe_path: Path to ffprobe executable

    Returns:
        Tuple of (width, height)

    Raises:
        subprocess.CalledProcessError: If ffprobe fails
    """
    cmd = [
        ffprobe_path, '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'csv=p=0',
        str(file_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    width, height = result.stdout.strip().split(',')
    return int(width), int(height)


def check_ffmpeg_installed(ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe") -> bool:
    """Check if FFmpeg and ffprobe are installed.

    Args:
        ffmpeg_path: Path to ffmpeg executable
        ffprobe_path: Path to ffprobe executable

    Returns:
        True if both are available, False otherwise
    """
    try:
        subprocess.run([ffmpeg_path, '-version'], capture_output=True, check=True)
        subprocess.run([ffprobe_path, '-version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def check_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    """Check if Ollama server is running.

    Args:
        base_url: Ollama API base URL

    Returns:
        True if Ollama is running, False otherwise
    """
    try:
        import requests
        response = requests.get(f"{base_url}/api/tags", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "1m 23s")
    """
    minutes = int(seconds // 60)
    secs = int(seconds % 60)

    if minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def get_file_size_mb(file_path: Path) -> float:
    """Get file size in megabytes.

    Args:
        file_path: Path to file

    Returns:
        File size in MB
    """
    size_bytes = Path(file_path).stat().st_size
    return size_bytes / (1024 * 1024)


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, create if it doesn't.

    Args:
        path: Directory path

    Returns:
        Path object for the directory
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_temp_files(temp_dir: Path, pattern: str = "*"):
    """Clean up temporary files matching pattern.

    Args:
        temp_dir: Temporary directory
        pattern: Glob pattern for files to remove
    """
    temp_dir = Path(temp_dir)

    if not temp_dir.exists():
        return

    for file_path in temp_dir.glob(pattern):
        if file_path.is_file():
            file_path.unlink()


def validate_environment() -> Tuple[bool, list]:
    """Validate that all required tools and settings are configured.

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check FFmpeg
    if not check_ffmpeg_installed():
        errors.append("FFmpeg/ffprobe not found in PATH")

    # Check Ollama
    if not check_ollama_running():
        errors.append("Ollama server not running (start with 'ollama serve')")

    # Check environment variables
    required_vars = ["PEXELS_API_KEY", "PIPER_MODEL_PATH"]
    for var in required_vars:
        if not os.getenv(var):
            errors.append(f"Environment variable {var} not set")

    # Check Piper model exists
    piper_model = os.getenv("PIPER_MODEL_PATH")
    if piper_model and not Path(piper_model).exists():
        errors.append(f"Piper model not found at: {piper_model}")

    return (len(errors) == 0, errors)
