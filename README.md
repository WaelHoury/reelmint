# YouTube Video Generator

Generate YouTube videos from text prompts using local AI.

## Features

- **Scene-based Planning**: LLM generates structured scenes with narration and visual descriptions
- **Local TTS**: Piper TTS for commercial-safe voice generation
- **Semantic Video Matching**: OpenCLIP scores videos for relevance to narration
- **Automated Assembly**: FFmpeg handles all video/audio processing

## Requirements

### Hardware
- GPU with 8GB+ VRAM (tested on RTX 5060 Ti)
- 32GB RAM recommended

### Software
- Python 3.10+
- FFmpeg (with libx264, AAC support)
- Ollama (with Qwen3:8b model)
- Piper TTS

## Installation

### 1. Install FFmpeg
Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.

### 2. Install Ollama
Download from [ollama.ai](https://ollama.ai) and pull the model:
```bash
ollama pull qwen2.5:3b
```

### 3. Install Piper TTS
Download from [Piper GitHub releases](https://github.com/rhasspy/piper/releases) and download a voice model.

### 4. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 5. Configure Environment
```bash
cp .env.example .env
# Edit .env with your API keys and paths
```

### 6. Get Pexels API Key
Sign up at [pexels.com/api](https://www.pexels.com/api/) and add key to `.env`

## Usage

### Phase 1: Basic Generation

```bash
python generate_video.py --prompt "explain photosynthesis in plants" --duration 1.0 --output video.mp4
```

**Arguments:**
- `--prompt`: Topic/script for the video
- `--duration`: Duration in minutes (e.g., 1.0 = 1 minute, 2.5 = 2.5 minutes)
- `--output`: Output video file path

**Output:**
- `video.mp4`: Final video with audio
- `video_credits.txt`: Attribution for all assets used

## Project Structure

```
youtube-video-generator/
├── generate_video.py       # Main orchestration script
├── scene_planner.py        # LLM scene generation
├── tts_generator.py        # Piper TTS integration
├── video_searcher.py       # Pexels API search
├── clip_matcher.py         # OpenCLIP video scoring
├── video_assembler.py      # FFmpeg operations
├── license_tracker.py      # Asset tracking
├── requirements.txt        # Python dependencies
├── .env                    # Configuration (create from .env.example)
└── README.md              # This file
```

## How It Works

1. **Scene Planning**: LLM breaks down prompt into 3-8 scenes with narration and visual keywords
2. **Audio Generation**: Piper TTS generates narration audio for each scene
3. **Video Search**: Pexels API finds candidate videos based on keywords
4. **Semantic Matching**: OpenCLIP scores videos against scene descriptions
5. **Video Assembly**: FFmpeg normalizes, trims, and concatenates best videos
6. **Final Muxing**: Audio and video combined into final MP4

## License

MIT License - See individual dependencies for their licenses.

## Credits

This project uses:
- [Piper TTS](https://github.com/rhasspy/piper) - MIT License
- [OpenCLIP](https://github.com/mlfoundations/open_clip) - MIT License
- [Pexels API](https://www.pexels.com/api/) - Free API with attribution
- [Ollama](https://ollama.ai) - MIT License
- [FFmpeg](https://ffmpeg.org) - LGPL/GPL
