"""Check that this machine is ready to generate videos.

Usage (from the repo root):
    python scripts/check_setup.py

Checks only what your .env actually uses: the LLM provider you picked, the
voice engine, FFmpeg, and your Pexels key. Nothing is downloaded or installed.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

results = []  # (status, name, detail) with status in {"ok", "warn", "fail"}


def check(name, critical=True):
    def wrap(fn):
        try:
            ok, detail = fn()
        except Exception as e:  # report, never crash the checker
            ok, detail = False, str(e)
        results.append(("ok" if ok else ("fail" if critical else "warn"), name, detail))
        return fn
    return wrap


def has_module(name):
    return importlib.util.find_spec(name) is not None


@check("Python 3.10+")
def _python():
    v = sys.version_info
    return v >= (3, 10), f"{v.major}.{v.minor}.{v.micro}"


@check("FFmpeg with subtitle support")
def _ffmpeg():
    ffmpeg = os.getenv("FFMPEG_PATH") or "ffmpeg"
    if not shutil.which(ffmpeg) and not Path(ffmpeg).exists():
        return False, "not found; install it (brew/apt/winget install ffmpeg)"
    filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=20).stdout
    if " ass " not in filters:
        return False, "found, but built without libass (captions need it)"
    return True, shutil.which(ffmpeg) or ffmpeg


@check("Python packages")
def _packages():
    needed = ["fastapi", "requests", "pydantic", "open_clip", "torch", "faster_whisper", "kokoro"]
    missing = [m for m in needed if not has_module(m)]
    if missing:
        return False, "missing: " + ", ".join(missing) + " (pip install -r requirements.txt)"
    return True, "installed"


@check("Pexels API key")
def _pexels():
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return False, "PEXELS_API_KEY is empty (free key: https://www.pexels.com/api/)"
    import requests
    r = requests.get("https://api.pexels.com/videos/search",
                     headers={"Authorization": key},
                     params={"query": "ocean", "per_page": 1}, timeout=15)
    if r.status_code == 200:
        return True, "valid"
    return False, f"Pexels rejected the key (HTTP {r.status_code})"


provider = (os.getenv("LLM_PROVIDER") or "ollama").strip().lower()


@check(f"LLM provider ({provider})")
def _llm():
    if provider == "ollama":
        import requests
        base = os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434"
        model = os.getenv("OLLAMA_MODEL") or "qwen2.5:3b"
        try:
            tags = requests.get(f"{base}/api/tags", timeout=5).json()
        except Exception:
            return False, f"Ollama isn't reachable at {base}; start it with `ollama serve`"
        names = {m.get("name", "") for m in tags.get("models", [])}
        if model in names or f"{model}:latest" in names:
            return True, f"{model} ready"
        return False, f"model '{model}' not pulled; run `ollama pull {model}`"
    if provider == "openrouter":
        return bool(os.getenv("OPENROUTER_API_KEY")), "OPENROUTER_API_KEY " + (
            "set" if os.getenv("OPENROUTER_API_KEY") else "is empty")
    if provider == "anthropic":
        if not has_module("anthropic"):
            return False, "pip install anthropic"
        return bool(os.getenv("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY " + (
            "set" if os.getenv("ANTHROPIC_API_KEY") else "is empty")
    if provider == "claude-code":
        exe = os.getenv("CLAUDE_CODE_PATH") or "claude"
        if not shutil.which(exe):
            return False, "Claude Code CLI not found; install it and run `claude` once to log in"
        version = subprocess.run([exe, "--version"], capture_output=True, text=True,
                                 timeout=20).stdout.strip()
        return True, f"{version} (uses the CLI's login)"
    return False, "LLM_PROVIDER must be ollama, openrouter, anthropic or claude-code"


@check("Kokoro voice (spaCy English model)")
def _kokoro():
    if not has_module("en_core_web_sm"):
        return False, "run `python -m spacy download en_core_web_sm`"
    return True, "ready"


@check("GPU acceleration", critical=False)
def _gpu():
    import torch
    if torch.cuda.is_available():
        return True, f"CUDA: {torch.cuda.get_device_name(0)}"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return True, "Apple Silicon GPU available"
    return False, "none; CPU works, just slower (Chatterbox voices need CUDA)"


@check("Background music", critical=False)
def _music():
    music_dir = REPO_ROOT / (os.getenv("MUSIC_DIR") or "music")
    tracks = [p for p in music_dir.rglob("*") if p.suffix.lower() in (".mp3", ".wav")]
    if tracks:
        return True, f"{len(tracks)} tracks"
    return False, "no tracks yet; add some to music/<mood>/ (see music/README.md)"


icons = {"ok": "  OK  ", "warn": " WARN ", "fail": " FAIL "}
print("\nAI Video Maker setup check\n")
for status, name, detail in results:
    print(f"[{icons[status]}] {name}: {detail}")

failed = [r for r in results if r[0] == "fail"]
print()
if failed:
    print(f"{len(failed)} problem(s) to fix before generating videos.")
    sys.exit(1)
print("Ready. Try: python generate_video.py --prompt \"why octopuses have three hearts\" "
      "--duration 1 --orientation portrait --output octopus.mp4")
