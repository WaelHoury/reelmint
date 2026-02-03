# Quick Start Guide

Get your first video generated in 10 minutes.

## Prerequisites

- Windows with NVIDIA GPU
- Python 3.10+ installed
- 20GB free disk space

## Installation (5 minutes)

### 1. Install Required Software

```bash
# Install FFmpeg (using Chocolatey)
choco install ffmpeg

# Install Ollama
# Download from https://ollama.ai and run installer

# Pull AI model
ollama pull qwen2.5:3b
```

### 2. Install Piper TTS

1. Download from [Piper Releases](https://github.com/rhasspy/piper/releases)
2. Extract to `C:\Program Files\Piper`
3. Download voice model:
   - [en_US-lessac-medium.onnx](https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx)
   - [en_US-lessac-medium.onnx.json](https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json)
4. Save to `C:\projects\ai video maker\models\piper\`

### 3. Setup Python Project

```bash
cd "C:\projects\ai video maker"

# Create virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 4. Configure API Key

1. Get free API key from [Pexels](https://www.pexels.com/api/)
2. Copy `.env.example` to `.env`
3. Add your API key to `.env`:

```env
PEXELS_API_KEY=your_key_here
PIPER_MODEL_PATH=C:\projects\ai video maker\models\piper\en_US-lessac-medium.onnx
```

### 5. Test Setup

```bash
python test_setup.py
```

If all tests pass, you're ready!

## Generate Your First Video (2 minutes)

```bash
python generate_video.py --prompt "how rainbows form in the sky" --duration 1.0 --output my_first_video.mp4
```

This will:
1. Plan scenes using AI
2. Generate narration with TTS
3. Search for matching stock videos
4. Score videos with AI for relevance
5. Assemble final video with audio
6. Create credits file

**Output:**
- `my_first_video.mp4` - Your AI-generated video
- `my_first_video_credits.txt` - Attribution for stock footage

## Next Steps

### Try Different Topics

```bash
# Educational
python generate_video.py --prompt "explain photosynthesis" --duration 1.5 --output photosynthesis.mp4

# How-to
python generate_video.py --prompt "how coffee is made from bean to cup" --duration 2.0 --output coffee.mp4

# Documentary style
python generate_video.py --prompt "the life cycle of butterflies" --duration 2.5 --output butterflies.mp4
```

### Adjust Duration

- `--duration 0.5` = 30 seconds (3 scenes)
- `--duration 1.0` = 1 minute (4 scenes)
- `--duration 2.0` = 2 minutes (5 scenes)
- `--duration 5.0` = 5 minutes (8 scenes)

### Keep Temp Files for Debugging

```bash
python generate_video.py --prompt "your topic" --duration 1.0 --output video.mp4 --keep-temp
```

This keeps intermediate files in `temp/video_<name>/` for inspection.

## Common Issues

### "CUDA out of memory"
- Close other GPU applications
- Try shorter duration (--duration 0.5)

### "Ollama connection refused"
```bash
# Start Ollama server
ollama serve
```

### "No videos found"
- Try simpler, more common keywords
- Check your Pexels API key
- Free tier: 50 requests/hour limit

### "FFmpeg not found"
```bash
# Verify installation
ffmpeg -version

# Add to PATH if needed
```

## Tips for Best Results

1. **Be specific in prompts**: "how bees pollinate flowers" > "bees"
2. **Use visual topics**: Things that can be shown with stock footage
3. **Start short**: Test with 30-60 seconds first
4. **Check credits**: Always include credits.txt when publishing

## What's Happening Behind the Scenes?

```
Prompt → AI Plans Scenes → TTS Generates Audio → Search Stock Videos
                ↓
        AI Scores Videos → Selects Best Match → Assembles Video
                ↓
            Final MP4 + Credits
```

## File Structure After First Run

```
C:\projects\ai video maker\
├── my_first_video.mp4          # Your video!
├── my_first_video_credits.txt  # Required attribution
├── cache/                      # AI model caches (speeds up future runs)
└── temp/                       # Temp files (auto-deleted unless --keep-temp)
```

## Ready for More?

- Read [SETUP.md](SETUP.md) for detailed configuration
- Read [README.md](README.md) for architecture overview
- Experiment with different Piper voices
- Move to Phase 2 for API + job queue (see spec)

## Get Help

If something doesn't work:
1. Run `python test_setup.py` to diagnose
2. Check [SETUP.md](SETUP.md) troubleshooting section
3. Verify `.env` configuration
4. Check that Ollama is running

Enjoy creating AI-generated videos!
