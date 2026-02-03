"""Utility functions for video generation."""

import subprocess
from pathlib import Path
from typing import Tuple
import os


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
