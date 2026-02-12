"""Media processing modules (TTS, video search, assembly, effects)."""

# Re-export from root modules for backward compatibility
import sys
from pathlib import Path

# Add parent directory to path for imports from root
root_dir = Path(__file__).parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from tts_generator import TTSGenerator, transcribe_with_word_timestamps
from video_searcher import VideoSearcher, VideoInfo, CachedVideoSearcher
from clip_matcher import CLIPMatcher
from video_assembler import VideoAssembler, get_resolution
from effects_engine import EffectsEngine
from subtitle_generator import SubtitleGenerator
from audio_mixer import AudioMixer
from music_selector import MusicSelector

__all__ = [
    'TTSGenerator',
    'transcribe_with_word_timestamps',
    'VideoSearcher',
    'CachedVideoSearcher',
    'VideoInfo',
    'CLIPMatcher',
    'VideoAssembler',
    'get_resolution',
    'EffectsEngine',
    'SubtitleGenerator',
    'AudioMixer',
    'MusicSelector',
]
