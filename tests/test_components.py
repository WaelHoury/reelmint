"""Test individual components of the video generator.

Useful for debugging and verifying each module works independently.

These are live integration checks: they call Ollama, Piper, Pexels and
ffmpeg, download videos and write files into the working directory. Under
pytest they only run when ``RUN_COMPONENT_TESTS=1`` is set *and* the
service/dependency each one needs is available; otherwise they are skipped.
They can also be run directly: ``python tests/test_components.py --component all``.
"""

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for collection
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if load_dotenv is not None:
    load_dotenv()


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _ollama_running() -> bool:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        with urlopen(f"{base_url}/api/tags", timeout=2):
            return True
    except Exception:
        return False


def _piper_available() -> bool:
    exe = os.getenv("PIPER_EXECUTABLE", "piper.exe" if os.name == "nt" else "piper")
    model = os.getenv("PIPER_MODEL_PATH")
    exe_ok = Path(exe).exists() or shutil.which(exe) is not None
    return exe_ok and bool(model) and Path(model).exists()


def _ffmpeg_available() -> bool:
    return shutil.which(os.getenv("FFMPEG_PATH", "ffmpeg")) is not None and \
        shutil.which(os.getenv("FFPROBE_PATH", "ffprobe")) is not None


_OPT_IN = os.getenv("RUN_COMPONENT_TESTS") == "1"
_opt_in = pytest.mark.skipif(
    not _OPT_IN, reason="live component test; set RUN_COMPONENT_TESTS=1 to run"
)
_needs_pexels = pytest.mark.skipif(
    not (os.getenv("PEXELS_API_KEY") and _has_module("video_searcher") and _has_module("requests")),
    reason="needs PEXELS_API_KEY and video_searcher dependencies",
)


@_opt_in
@pytest.mark.skipif(
    not (_OPT_IN and _has_module("ollama") and _ollama_running()),
    reason="needs the ollama package and a running Ollama server",
)
def test_scene_planner():
    """Test scene planning with Ollama."""
    print("\n" + "="*70)
    print("Testing Scene Planner")
    print("="*70 + "\n")

    from scene_planner import ScenePlanner

    planner = ScenePlanner(
        model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )

    prompt = "how plants convert sunlight into energy"
    duration = 1.0

    print(f"Generating plan for: {prompt}")
    print(f"Duration: {duration} minutes\n")

    scenes = planner.generate(prompt, duration)

    print(planner.preview_plan(scenes))
    print(f"\n✓ Successfully generated {len(scenes)} scenes")


@_opt_in
@pytest.mark.skipif(not _piper_available(), reason="needs Piper executable and PIPER_MODEL_PATH")
def test_tts():
    """Test TTS generation."""
    print("\n" + "="*70)
    print("Testing TTS Generator")
    print("="*70 + "\n")

    from tts_generator import TTSGenerator

    tts = TTSGenerator(
        piper_executable=os.getenv("PIPER_EXECUTABLE", "piper.exe" if os.name == "nt" else "piper"),
        model_path=os.getenv("PIPER_MODEL_PATH"),
        ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg")
    )

    text = "This is a test of the text to speech system. It should sound natural."
    output = Path("test_tts.wav")

    print(f"Generating audio: {text}")
    print(f"Output: {output}\n")

    tts.generate(text, output)

    duration = tts.get_audio_duration(output)

    print(f"✓ Generated audio file: {output}")
    print(f"  Duration: {duration:.2f} seconds")
    print(f"  Size: {output.stat().st_size / 1024:.1f} KB")


@_opt_in
@_needs_pexels
def test_video_search():
    """Test video search."""
    print("\n" + "="*70)
    print("Testing Video Searcher")
    print("="*70 + "\n")

    from video_searcher import VideoSearcher

    searcher = VideoSearcher(api_key=os.getenv("PEXELS_API_KEY"))

    keywords = ["ocean", "waves", "sunset"]

    print(f"Searching for: {keywords}\n")

    videos = searcher.search(keywords, max_results=5)

    print(f"✓ Found {len(videos)} videos:\n")

    for i, video in enumerate(videos, 1):
        print(f"{i}. {video.author}")
        print(f"   Duration: {video.duration}s")
        print(f"   URL: {video.url}")
        print()


@_opt_in
@_needs_pexels
@pytest.mark.skipif(
    not (_has_module("open_clip") and _has_module("torch")),
    reason="needs open_clip and torch",
)
def test_clip_matching():
    """Test CLIP video scoring (requires downloaded video)."""
    print("\n" + "="*70)
    print("Testing CLIP Matcher")
    print("="*70 + "\n")

    from clip_matcher import CLIPMatcher
    from video_searcher import VideoSearcher

    # Download a test video
    print("Downloading test video...")
    searcher = VideoSearcher(api_key=os.getenv("PEXELS_API_KEY"))
    videos = searcher.search(["ocean"], max_results=3)

    if not videos:
        print("✗ No videos found")
        return

    # Download top 3
    temp_dir = Path("temp/clip_test")
    temp_dir.mkdir(parents=True, exist_ok=True)

    video_paths = []
    for i, video in enumerate(videos[:3]):
        path = temp_dir / f"test_{i}.mp4"
        searcher.download(video, path)
        video_paths.append(path)

    print(f"Downloaded {len(video_paths)} videos\n")

    # Score them
    print("Loading CLIP model...")
    matcher = CLIPMatcher(
        model_name=os.getenv("CLIP_MODEL", "ViT-B-32"),
        pretrained=os.getenv("CLIP_PRETRAINED", "laion2b_s34b_b79k"),
        cache_dir=Path(os.getenv("CACHE_DIR", "cache"))
    )

    scene_text = "beautiful ocean waves crashing on the beach at sunset"

    print(f"Scoring videos against: {scene_text}\n")

    scores = matcher.score_videos(video_paths, scene_text)

    print("✓ Scores:")
    for path, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        print(f"  {path.name}: {score:.4f}")


@_opt_in
@_needs_pexels
@pytest.mark.skipif(not _ffmpeg_available(), reason="needs ffmpeg and ffprobe")
def test_video_assembly():
    """Test video assembly operations."""
    print("\n" + "="*70)
    print("Testing Video Assembler")
    print("="*70 + "\n")

    from video_assembler import VideoAssembler
    from video_searcher import VideoSearcher

    # Download test video
    print("Downloading test video...")
    searcher = VideoSearcher(api_key=os.getenv("PEXELS_API_KEY"))
    videos = searcher.search(["nature"], max_results=1)

    if not videos:
        print("✗ No videos found")
        return

    temp_dir = Path("temp/assembly_test")
    temp_dir.mkdir(parents=True, exist_ok=True)

    video_path = temp_dir / "test.mp4"
    searcher.download(videos[0], video_path)

    assembler = VideoAssembler(
        ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
        ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
    )

    # Test normalization
    print("\nTesting normalization...")
    normalized = temp_dir / "normalized.mp4"
    assembler.normalize_video(video_path, normalized)
    print(f"✓ Normalized: {normalized}")

    # Test trimming
    print("\nTesting trim...")
    trimmed = temp_dir / "trimmed.mp4"
    assembler.trim_video(normalized, trimmed, start_sec=0, duration_sec=5)

    duration = assembler.get_duration(trimmed)
    print(f"✓ Trimmed to {duration:.2f} seconds")


def main():
    parser = argparse.ArgumentParser(description="Test individual components")

    parser.add_argument(
        "--component",
        choices=["planner", "tts", "search", "clip", "assembly", "all"],
        default="all",
        help="Component to test"
    )

    args = parser.parse_args()

    tests = {
        "planner": test_scene_planner,
        "tts": test_tts,
        "search": test_video_search,
        "clip": test_clip_matching,
        "assembly": test_video_assembly
    }

    if args.component == "all":
        for name, test_func in tests.items():
            try:
                test_func()
            except Exception as e:
                print(f"\n✗ {name} test failed: {e}")
                import traceback
                traceback.print_exc()
    else:
        tests[args.component]()


if __name__ == "__main__":
    main()
