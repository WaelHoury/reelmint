# Setup Guide - YouTube Video Generator

Complete setup instructions for Windows with RTX 5060 Ti.

## Prerequisites

- Windows 10/11
- NVIDIA GPU with 8GB+ VRAM
- Python 3.10 or higher
- 20GB free disk space (for models and temp files)

## Step-by-Step Setup

### 1. Install Python

Download and install Python 3.10+ from [python.org](https://www.python.org/downloads/)

**Important:** Check "Add Python to PATH" during installation.

Verify installation:
```bash
python --version
```

### 2. Install FFmpeg

**Option A: Using Chocolatey (Recommended)**
```bash
choco install ffmpeg
```

**Option B: Manual Installation**
1. Download from [ffmpeg.org](https://ffmpeg.org/download.html)
2. Extract to `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to PATH

Verify installation:
```bash
ffmpeg -version
ffprobe -version
```

### 3. Install Ollama

1. Download from [ollama.ai](https://ollama.ai)
2. Run installer
3. Pull the model:
```bash
ollama pull qwen2.5:3b
```

Verify installation:
```bash
ollama list
```

Start Ollama server (should auto-start on Windows):
```bash
ollama serve
```

### 4. Install Piper TTS

1. Download Windows release from [Piper GitHub](https://github.com/rhasspy/piper/releases)
2. Extract to `C:\Program Files\Piper`
3. Download a voice model:
   - Go to [Piper Voice Models](https://huggingface.co/rhasspy/piper-voices/tree/main)
   - Download `en_US-lessac-medium.onnx` and `en_US-lessac-medium.onnx.json`
   - Save to `C:\projects\ai video maker\models\piper\`

Directory structure:
```
C:\projects\ai video maker\
└── models\
    └── piper\
        ├── en_US-lessac-medium.onnx
        └── en_US-lessac-medium.onnx.json
```

Test Piper:
```bash
echo "Hello world" | piper --model C:\projects\ai video maker\models\piper\en_US-lessac-medium.onnx --output_file test.wav
```

### 5. Get Pexels API Key

1. Sign up at [pexels.com](https://www.pexels.com/api/)
2. Get your API key from the dashboard
3. Save it for the next step

### 6. Setup Python Project

Navigate to project directory:
```bash
cd "C:\projects\ai video maker"
```

Create virtual environment:
```bash
python -m venv venv
```

Activate virtual environment:
```bash
venv\Scripts\activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

### 7. Configure Environment

Create `.env` file from template:
```bash
copy .env.example .env
```

Edit `.env` file with your settings:

```env
# Pexels API
PEXELS_API_KEY=your_actual_api_key_here

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b

# Piper TTS
PIPER_MODEL_PATH=C:\projects\ai video maker\models\piper\en_US-lessac-medium.onnx
PIPER_EXECUTABLE=piper.exe

# OpenCLIP
CLIP_MODEL=ViT-B-32
CLIP_PRETRAINED=laion2b_s34b_b79k

# Directories
TEMP_DIR=C:\projects\ai video maker\temp
CACHE_DIR=C:\projects\ai video maker\cache
OUTPUT_DIR=C:\projects\ai video maker\output

# FFmpeg (leave blank if in PATH)
FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
```

Create required directories:
```bash
mkdir temp cache output models\piper
```

### 8. Test Installation

Run the test script:
```bash
python test_setup.py
```

This will verify:
- FFmpeg is installed
- Ollama is running
- Piper TTS works
- Pexels API key is valid
- OpenCLIP model can load
- GPU is available

## First Video Generation

Try generating a short test video:

```bash
python generate_video.py --prompt "how plants grow from seeds" --duration 0.5 --output test_video.mp4
```

This should create:
- `test_video.mp4` - Your first AI-generated video!
- `test_video_credits.txt` - Attribution for stock footage
- `test_video_credits.json` - Machine-readable credits

## Troubleshooting

### Error: "CUDA out of memory"

**Solution:** Only one model can run at a time with 8GB VRAM. The script handles this automatically, but ensure:
- No other GPU-intensive programs are running
- Close Chrome/browsers with hardware acceleration
- Reduce video candidates from 5 to 3 in generate_video.py

### Error: "Ollama connection refused"

**Solution:**
```bash
# Start Ollama server
ollama serve

# In another terminal, verify it's running
curl http://localhost:11434/api/tags
```

### Error: "Piper command not found"

**Solution:**
- Ensure `piper.exe` is in PATH or specify full path in `.env`
- Try: `where piper` to find its location
- Update `PIPER_EXECUTABLE` in `.env` with full path

### Error: "FFmpeg concat failed"

**Solution:**
- This usually means videos have different formats
- The normalization step should prevent this
- Check temp files to see if normalization completed
- Try with `--keep-temp` flag to inspect intermediate files

### Error: "No videos found for keywords"

**Solution:**
- Check Pexels API key is valid
- Try different/simpler keywords
- Check your API rate limits (50 requests/hour for free tier)

### Videos out of sync with audio

**Solution:**
- Check that audio normalization completed (48kHz, stereo)
- Verify FFmpeg concat used `-c copy` (should be automatic)
- Try regenerating with `--keep-temp` to inspect audio files

## GPU Memory Optimization

If you encounter memory issues:

1. **Close unnecessary programs**
2. **Lower CLIP batch size** (edit clip_matcher.py if needed)
3. **Use smaller Ollama model**:
   ```bash
   ollama pull qwen2.5:1.5b
   # Update OLLAMA_MODEL in .env
   ```

## Next Steps

Once Phase 1 works:
- Try longer videos (2-5 minutes)
- Experiment with different prompts
- Download additional Piper voices
- Move to Phase 2 (API + job queue)

## Additional Resources

- [Piper Voice Samples](https://rhasspy.github.io/piper-samples/)
- [Pexels License Info](https://www.pexels.com/license/)
- [OpenCLIP Models](https://github.com/mlfoundations/open_clip)
- [FFmpeg Documentation](https://ffmpeg.org/documentation.html)
