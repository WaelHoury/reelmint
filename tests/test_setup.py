"""Test script to verify installation and configuration.

Run this after setup to ensure all components work correctly.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import tempfile

# Load environment
load_dotenv()

print("=" * 70)
print("YouTube Video Generator - Setup Verification")
print("=" * 70)
print()

errors = []
warnings = []
successes = []


def test_item(name: str, test_func, critical: bool = True):
    """Run a test and track results."""
    try:
        print(f"Testing {name}...", end=" ")
        result = test_func()

        if result:
            print("✓ PASS")
            successes.append(name)
        else:
            print("✗ FAIL")
            if critical:
                errors.append(f"{name} failed")
            else:
                warnings.append(f"{name} failed (non-critical)")

    except Exception as e:
        print(f"✗ ERROR: {e}")
        if critical:
            errors.append(f"{name}: {e}")
        else:
            warnings.append(f"{name}: {e}")


# Test 1: Python version
def test_python_version():
    version = sys.version_info
    return version.major == 3 and version.minor >= 10


# Test 2: FFmpeg
def test_ffmpeg():
    from utils import check_ffmpeg_installed
    return check_ffmpeg_installed(
        os.getenv("FFMPEG_PATH", "ffmpeg"),
        os.getenv("FFPROBE_PATH", "ffprobe")
    )


# Test 3: Ollama
def test_ollama():
    from utils import check_ollama_running
    return check_ollama_running(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))


# Test 4: Ollama model
def test_ollama_model():
    import ollama
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    try:
        models = ollama.list()
        # Handle different response formats
        if 'models' in models:
            model_list = models['models']
            # Check both 'name' and 'model' keys
            return any(
                model in m.get('name', '') or model in m.get('model', '')
                for m in model_list
            )
        return False
    except Exception as e:
        print(f"\n  Debug: Ollama list error: {e}")
        return False


# Test 5: Pexels API
def test_pexels():
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        return False

    from video_searcher import VideoSearcher
    searcher = VideoSearcher(api_key)
    return searcher.test_api_key()


# Test 6: Piper TTS
def test_piper():
    model_path = os.getenv("PIPER_MODEL_PATH")
    if not model_path:
        print("\n  Error: PIPER_MODEL_PATH not set in .env")
        return False

    if not Path(model_path).exists():
        print(f"\n  Error: Model file not found at {model_path}")
        print(f"  Download voice model from:")
        print(f"  https://huggingface.co/rhasspy/piper-voices/tree/main")
        return False

    piper_exe = os.getenv("PIPER_EXECUTABLE", "piper.exe")

    # Check if piper.exe exists in PATH or as full path
    if not Path(piper_exe).exists():
        # Try to find in PATH
        import shutil
        if not shutil.which(piper_exe):
            print(f"\n  Error: {piper_exe} not found in PATH")
            print(f"  Please install Piper TTS or set full path in .env")
            print(f"  Download from: https://github.com/rhasspy/piper/releases")
            print(f"  Or run: python install_piper.py")
            return False

    # Test Piper can run
    from tts_generator import TTSGenerator
    tts = TTSGenerator(
        piper_executable=piper_exe,
        model_path=model_path
    )

    return tts.test_installation()


# Test 7: PyTorch + CUDA
def test_pytorch_cuda():
    import torch
    is_available = torch.cuda.is_available()

    if not is_available:
        print("\n  Note: CUDA not available - will use CPU (slower)")
        print("  To enable GPU acceleration:")
        print("  1. Install NVIDIA drivers: https://www.nvidia.com/drivers")
        print("  2. Reinstall PyTorch with CUDA:")
        print("     pip uninstall torch torchvision")
        print("     pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121")

    return is_available


# Test 8: OpenCLIP
def test_openclip():
    import open_clip
    model_name = os.getenv("CLIP_MODEL", "ViT-B-32")
    pretrained = os.getenv("CLIP_PRETRAINED", "laion2b_s34b_b79k")

    try:
        model, _, _ = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained,
            device='cpu'  # Test on CPU to avoid CUDA issues
        )
        return True
    except Exception:
        return False


# Test 9: Required directories
def test_directories():
    temp_dir = Path(os.getenv("TEMP_DIR", "temp"))
    cache_dir = Path(os.getenv("CACHE_DIR", "cache"))
    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))

    temp_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    return all([temp_dir.exists(), cache_dir.exists(), output_dir.exists()])


# Test 10: Environment variables
def test_env_vars():
    required = ["PEXELS_API_KEY", "PIPER_MODEL_PATH"]
    return all(os.getenv(var) for var in required)


# Run all tests
print("\n" + "=" * 70)
print("RUNNING TESTS")
print("=" * 70 + "\n")

test_item("Python 3.10+", test_python_version, critical=True)
test_item("Environment variables", test_env_vars, critical=True)
test_item("FFmpeg installed", test_ffmpeg, critical=True)
test_item("Ollama running", test_ollama, critical=True)
test_item("Ollama model downloaded", test_ollama_model, critical=True)
test_item("Pexels API key", test_pexels, critical=True)
test_item("Piper TTS", test_piper, critical=True)
test_item("PyTorch CUDA support", test_pytorch_cuda, critical=False)
test_item("OpenCLIP model", test_openclip, critical=True)
test_item("Required directories", test_directories, critical=True)

# Print summary
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70 + "\n")

print(f"✓ Passed: {len(successes)}")
print(f"✗ Failed: {len(errors)}")
print(f"⚠ Warnings: {len(warnings)}")
print()

if errors:
    print("CRITICAL ERRORS:")
    for error in errors:
        print(f"  - {error}")
    print()

if warnings:
    print("WARNINGS:")
    for warning in warnings:
        print(f"  - {warning}")
    print()

if not errors:
    print("=" * 70)
    print("✓ ALL TESTS PASSED!")
    print("=" * 70)
    print()
    print("You're ready to generate videos!")
    print()
    print("Try running:")
    print('  python generate_video.py --prompt "how bees make honey" --duration 1.0 --output test.mp4')
    print()
    sys.exit(0)
else:
    print("=" * 70)
    print("✗ SETUP INCOMPLETE")
    print("=" * 70)
    print()
    print("Please fix the errors above and run this test again.")
    print("See SETUP.md for detailed instructions.")
    print()
    sys.exit(1)
