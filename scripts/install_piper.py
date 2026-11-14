"""Automated installer for Piper TTS on Windows.

Downloads and sets up Piper TTS with a default voice model.
"""

import requests
import zipfile
from pathlib import Path
import platform
import sys


def download_file(url: str, output_path: Path, description: str = "file"):
    """Download file with progress indication."""
    print(f"Downloading {description}...")

    response = requests.get(url, stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    block_size = 8192
    downloaded = 0

    with open(output_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=block_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)

                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    print(f"\r  Progress: {percent:.1f}%", end='', flush=True)

    print(f"\r  Progress: 100.0%")
    print(f"✓ Downloaded {description}")


def install_piper():
    """Download and install Piper TTS for Windows."""
    print("=" * 70)
    print("Piper TTS Installer")
    print("=" * 70)
    print()

    # Check platform
    if platform.system() != "Windows":
        print("Error: This installer is for Windows only")
        print("For other platforms, see: https://github.com/rhasspy/piper")
        return 1

    # Create installation directory
    install_dir = Path("piper")
    install_dir.mkdir(exist_ok=True)

    # Download Piper Windows release
    piper_url = "https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_windows_amd64.zip"
    zip_path = install_dir / "piper_windows.zip"

    try:
        download_file(piper_url, zip_path, "Piper TTS")

        # Extract
        print("\nExtracting Piper TTS...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(install_dir)
        print("✓ Extracted Piper TTS")

        # Clean up zip
        zip_path.unlink()

        # Find piper.exe
        piper_exe = None
        for exe_path in install_dir.rglob("piper.exe"):
            piper_exe = exe_path
            break

        if not piper_exe:
            print("✗ Error: piper.exe not found in extracted files")
            return 1

        print(f"✓ Piper installed at: {piper_exe.absolute()}")

    except Exception as e:
        print(f"\n✗ Error downloading/extracting Piper: {e}")
        return 1

    # Download default voice model
    print("\nDownloading voice model (en_US-lessac-medium)...")

    models_dir = Path("models/piper")
    models_dir.mkdir(parents=True, exist_ok=True)

    voice_base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

    model_files = {
        "en_US-lessac-medium.onnx": f"{voice_base_url}/en_US-lessac-medium.onnx",
        "en_US-lessac-medium.onnx.json": f"{voice_base_url}/en_US-lessac-medium.onnx.json"
    }

    try:
        for filename, url in model_files.items():
            output_path = models_dir / filename
            download_file(url, output_path, filename)

        print("✓ Voice model installed")

    except Exception as e:
        print(f"\n✗ Error downloading voice model: {e}")
        print("You can manually download from:")
        print("https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium")
        return 1

    # Update .env file
    print("\nUpdating .env configuration...")

    env_file = Path(".env")
    if not env_file.exists():
        print("✗ Warning: .env file not found")
        print("Please create .env from .env.example")

    # Read current .env
    env_lines = []
    if env_file.exists():
        with open(env_file, 'r') as f:
            env_lines = f.readlines()

    # Update PIPER paths
    piper_exe_abs = piper_exe.absolute()
    model_path_abs = (models_dir / "en_US-lessac-medium.onnx").absolute()

    updated_lines = []
    found_piper_exe = False
    found_piper_model = False

    for line in env_lines:
        if line.startswith("PIPER_EXECUTABLE="):
            updated_lines.append(f"PIPER_EXECUTABLE={piper_exe_abs}\n")
            found_piper_exe = True
        elif line.startswith("PIPER_MODEL_PATH="):
            updated_lines.append(f"PIPER_MODEL_PATH={model_path_abs}\n")
            found_piper_model = True
        else:
            updated_lines.append(line)

    # Add if not found
    if not found_piper_exe:
        updated_lines.append(f"PIPER_EXECUTABLE={piper_exe_abs}\n")
    if not found_piper_model:
        updated_lines.append(f"PIPER_MODEL_PATH={model_path_abs}\n")

    # Write back
    with open(env_file, 'w') as f:
        f.writelines(updated_lines)

    print("✓ Updated .env file")

    # Final summary
    print("\n" + "=" * 70)
    print("✓ INSTALLATION COMPLETE")
    print("=" * 70)
    print()
    print("Piper TTS has been installed successfully!")
    print()
    print(f"Executable: {piper_exe_abs}")
    print(f"Voice model: {model_path_abs}")
    print()
    print("Run scripts/check_setup.py to verify the installation:")
    print("  python scripts/check_setup.py")
    print()

    return 0


if __name__ == "__main__":
    try:
        exit_code = install_piper()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
