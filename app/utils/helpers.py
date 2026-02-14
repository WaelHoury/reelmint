"""Utility functions for video generation."""

import re
import shutil
import subprocess
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Tuple
import os

import redis as redis_lib

# Lazy-initialized Redis connection for distributed locks
_lock_redis: Optional[redis_lib.Redis] = None


def _get_lock_redis() -> redis_lib.Redis:
    """Get or create the Redis connection used for distributed locks."""
    global _lock_redis
    if _lock_redis is None:
        from app.core.config import settings
        _lock_redis = redis_lib.from_url(settings.redis_url)
    return _lock_redis


@contextmanager
def project_lock(project_id: str, timeout: int = 300, blocking_timeout: int = 10):
    """Acquire a Redis-based distributed lock for a project directory.

    Prevents concurrent editor operations (render, subtitle regen, audio remix)
    from corrupting shared files like project.json, subtitles.ass, audio_final.wav.

    Args:
        project_id: Unique identifier for the project (video_id, session_id, or project dir name)
        timeout: Auto-release lock after this many seconds (safety net)
        blocking_timeout: Max seconds to wait for lock acquisition

    Raises:
        HTTPException(409) if lock cannot be acquired within blocking_timeout.
    """
    from fastapi import HTTPException

    r = _get_lock_redis()
    lock = r.lock(f"project_lock:{project_id}", timeout=timeout, blocking_timeout=blocking_timeout)
    acquired = lock.acquire(blocking=True)
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail="Another edit operation is in progress for this video. Please wait and try again.",
        )
    try:
        yield
    finally:
        try:
            lock.release()
        except redis_lib.exceptions.LockNotOwnedError:
            pass  # Lock expired before release (timed out)


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string for safe use as a filename.

    Strips path separators, illegal filesystem characters, control characters,
    and leading dots. Falls back to 'untitled' if the result is empty.
    """
    name = unicodedata.normalize("NFKC", name)
    # Remove path separators and null bytes
    name = name.replace("/", "").replace("\\", "").replace("\0", "")
    # Remove Windows-illegal characters
    name = re.sub(r'[<>:"|?*]', '', name)
    # Remove control characters
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    # Remove leading dots (hidden files on Unix)
    name = name.lstrip('.')
    # Truncate
    if len(name) > max_length:
        name = name[:max_length].rstrip()
    return name or "untitled"


def parse_range_header(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    """Parse an HTTP Range header safely.

    Args:
        range_header: Raw Range header value (e.g. "bytes=0-1023")
        file_size: Total file size in bytes

    Returns:
        Tuple of (start, end) inclusive byte positions, or None if invalid.
    """
    try:
        if not range_header.startswith("bytes="):
            return None
        range_val = range_header[6:]
        if "," in range_val:
            return None  # Multi-range not supported
        parts = range_val.split("-", 1)
        if not parts[0] and not parts[1]:
            return None

        if not parts[0]:
            # Suffix range: "bytes=-500" means last 500 bytes
            suffix_len = int(parts[1])
            if suffix_len <= 0:
                return None
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        elif not parts[1]:
            # Open-ended: "bytes=500-"
            start = int(parts[0])
            end = file_size - 1
        else:
            start = int(parts[0])
            end = int(parts[1])

        if start < 0 or end < start or start >= file_size:
            return None
        end = min(end, file_size - 1)
        return (start, end)
    except (ValueError, IndexError):
        return None


def stream_file_response(file_path: Path, request) -> "StreamingResponse":
    """Stream a file with safe Range support and security headers.

    Args:
        file_path: Path to the file to stream
        request: FastAPI Request object (for Range header)

    Returns:
        StreamingResponse with appropriate headers
    """
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    headers_base = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Referrer-Policy": "no-referrer",
    }

    if range_header:
        parsed = parse_range_header(range_header, file_size)
        if parsed is None:
            raise HTTPException(
                status_code=416,
                detail="Invalid Range",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start, end = parsed
        chunk_size = end - start + 1

        def iter_range():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = f.read(min(8192, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_range(),
            status_code=206,
            media_type="video/mp4",
            headers={
                **headers_base,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(chunk_size),
            },
        )

    def iter_full():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        iter_full(),
        media_type="video/mp4",
        headers={
            **headers_base,
            "Content-Length": str(file_size),
        },
    )


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


def cleanup_old_temp_dirs(temp_dir: Path = None, max_age_hours: int = 48):
    """Remove temp subdirectories older than max_age_hours.

    Args:
        temp_dir: Root temp directory (default from settings)
        max_age_hours: Delete dirs older than this many hours
    """
    if temp_dir is None:
        from app.core.config import settings
        temp_dir = Path(settings.temp_dir)
    else:
        temp_dir = Path(temp_dir)

    if not temp_dir.exists():
        return

    cutoff = time.time() - (max_age_hours * 3600)
    for entry in temp_dir.iterdir():
        if entry.is_dir():
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass


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
